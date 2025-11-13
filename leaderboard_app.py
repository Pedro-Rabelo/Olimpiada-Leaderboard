import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh
import gspread
from gspread_dataframe import set_with_dataframe
from google.oauth2.service_account import Credentials
import pandas as pd
import zipfile
import unidecode
import re
import sys
from datetime import datetime
import os
import shutil
import json
import zoneinfo

# --- Configurações ---
# (Mantidos os nomes dos arquivos locais para o app funcionar localmente)
NOME_DO_ARQUIVO_JSON = "olimpiada-465219-c97ced7e5506.json"
KAGGLE_JSON_PATH = "kaggle.json"
SCOPES = ['https.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
NOME_DA_PLANILHA_SAIDA = "[NAO_EDITE]_leaderboard_geral_kaggle"
NOME_PLANILHA_INSCRICOES = "equipes_aprovadas"
NOME_ABA_INSCRICOES = "equipes_confirmadas"
# ALTERAR DE ACORDO COM OS SLUGS DOS NOVOS DESAFIOS!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
competitions = ["aprendizado-de-maquina-2-fase", "visao-computacional-2-fase", "linguagem-natural-2-fase"]
BR_TZ = zoneinfo.ZoneInfo("America/Sao_Paulo")

# --- LÓGICA DE AUTENTICAÇÃO INICIAL (KAGGLE) - MODIFICADA PARA DEPLOY ---
try:
    # Cria a pasta .kaggle se não existir (necessário no Streamlit Cloud)
    kaggle_dir = os.path.expanduser("~/.kaggle")
    os.makedirs(kaggle_dir, exist_ok=True)
    
    # Define o caminho do arquivo kaggle.json
    kaggle_json_path = os.path.join(kaggle_dir, "kaggle.json")

    # Verifica se o app está rodando localmente (e tem o arquivo) ou na nuvem
    if os.path.exists(KAGGLE_JSON_PATH):
        # Se estiver rodando local, apenas copia o arquivo local
        shutil.copy(KAGGLE_JSON_PATH, kaggle_json_path)
    else:
        # Se estiver na nuvem, pega de st.secrets e escreve o arquivo
        kaggle_credentials = {
            "username": st.secrets["kaggle"]["username"],
            "key": st.secrets["kaggle"]["key"]
        }
        with open(kaggle_json_path, "w") as f:
            json.dump(kaggle_credentials, f)
    
    # Define as permissões corretas para o arquivo
    os.chmod(kaggle_json_path, 0o600)
    
    from kaggle import api
except Exception as e:
    st.error(f"Falha na configuração do Kaggle: {e}. Você configurou os segredos 'kaggle' no Streamlit Cloud?")
    st.stop()
# --- FIM DA MODIFICAÇÃO ---


# --- Funções Auxiliares ---
def normalize_text(text):
    text = str(text)
    text = text.lower()
    text = unidecode.unidecode(text)
    return text

@st.cache_data(ttl=60)
def get_leaderboard_df(competition, path="."):
    zip_file_path = os.path.join(path, f"{competition}.zip")
    try:
        global api
        if os.path.exists(zip_file_path): os.remove(zip_file_path)
        api.competition_leaderboard_download(competition, path=path)
        with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
            csv_filename_in_zip = None
            for filename in zip_ref.namelist():
                if filename.endswith(".csv"):
                    csv_filename_in_zip = filename
                    break
            if not csv_filename_in_zip: raise FileNotFoundError(f"CSV não encontrado em {zip_file_path}")
            with zip_ref.open(csv_filename_in_zip) as csv_file:
                df = pd.read_csv(csv_file)
        df["competition"] = competition
        os.remove(zip_file_path)
        return df
    except Exception as e:
        print(f"[AVISO] Erro ao baixar/processar leaderboard '{competition}': {e}")
        if os.path.exists(zip_file_path):
            try: os.remove(zip_file_path)
            except Exception: pass
        return pd.DataFrame()

def safe_process_team_members(members_str): # Mantida caso precise no futuro, mas não usada no display
    if not isinstance(members_str, str): return ""
    try:
        members_list = eval(members_str)
        return ", ".join(map(str, members_list)) if isinstance(members_list, list) else members_str
    except: return members_str

# --- Funções de Escrita no Google Sheets ---
def write_to_sheet_overwrite(spreadsheet, sheet_name, df, log_list):
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        worksheet.clear()
        log_list.append(f"[INFO] Aba '{sheet_name}' limpa.")
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="1", cols="1")
    set_with_dataframe(worksheet, df, include_index=False, resize=True)
    log_list.append(f"[SUCESSO] Sobrescrito '{sheet_name}'.")

def append_to_backup_sheet(spreadsheet, sheet_name, df_new, log_list):
    if df_new.empty: return
    timestamp = datetime.now(BR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    df_to_append = df_new.copy()
    df_to_append.insert(0, 'Data Execução', timestamp)
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        existing_records = worksheet.get_all_records()
        if existing_records:
            df_existing = pd.DataFrame(existing_records).astype(str)
            df_to_append = df_to_append.astype(str)
            df_combined = pd.concat([df_existing, df_to_append], ignore_index=True)
        else: df_combined = df_to_append
        worksheet.clear()
        set_with_dataframe(worksheet, df_combined, include_index=False, resize=True)
        log_list.append(f"[SUCESSO] Backup '{sheet_name}'.")
    except gspread.exceptions.WorksheetNotFound:
        try:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="1", cols="1")
            set_with_dataframe(worksheet, df_to_append, include_index=False, resize=True)
            log_list.append(f"[SUCESSO] Criado backup '{sheet_name}'.")
        except Exception as create_err: log_list.append(f"[FALHA] Backup create '{sheet_name}': {create_err}")
    except Exception as general_err: log_list.append(f"[FALHA] Backup '{sheet_name}': {general_err}")


# --- FUNÇÃO PRINCIPAL DE BACKEND (COM CACHE) ---
@st.cache_data(ttl=300)
def buscar_e_processar_dados():
    log_mensagens_internas = []
    status_final_interno = "[SUCESSO]"
    df_publica_final = pd.DataFrame()
    df_privada_final = pd.DataFrame()
    try:
        log_mensagens_internas.append("[INFO] Iniciando busca e processamento de dados...")

        # --- AUTENTICAÇÃO GOOGLE - MODIFICADA PARA DEPLOY ---
        try:
            # Verifica se está rodando local (e tem o arquivo) ou na nuvem
            if os.path.exists(NOME_DO_ARQUIVO_JSON):
                # Se local, usa o arquivo
                creds = Credentials.from_service_account_file(NOME_DO_ARQUIVO_JSON, scopes=SCOPES)
            else:
                # Se na nuvem, usa o st.secrets
                google_creds_dict = dict(st.secrets["google_service_account"])
                creds = Credentials.from_service_account_info(google_creds_dict, scopes=SCOPES)
            
            gc = gspread.authorize(creds)
            log_mensagens_internas.append("[INFO] Autenticação Google OK.")
        except Exception as e:
            log_mensagens_internas.append(f"[FALHA] Auth Google: {e}. Verifique os segredos 'google_service_account'.")
            raise
        # --- FIM DA MODIFICAÇÃO ---

        # 1. Busca e unifica os leaderboards do Kaggle
        all_dfs = [get_leaderboard_df(comp) for comp in competitions]
        non_empty_dfs = [df for df in all_dfs if not df.empty]

        if not non_empty_dfs:
            log_mensagens_internas.append("[AVISO] Todos os leaderboards do Kaggle retornaram vazios.")
            raise SystemExit("Todos os leaderboards estão vazios.")

        df_all = pd.concat(non_empty_dfs, ignore_index=True)
        log_mensagens_internas.append(f"[INFO] {len(non_empty_dfs)} Leaderboards Kaggle processados.")

        # 2. Lê a planilha de equipes confirmadas
        try:
            inscriptions_spreadsheet = gc.open(NOME_PLANILHA_INSCRICOES)
            inscriptions_worksheet = inscriptions_spreadsheet.worksheet(NOME_ABA_INSCRICOES)
            records = inscriptions_worksheet.get_all_records()
            df_confirm = pd.DataFrame(records)
            log_mensagens_internas.append(f"[INFO] Planilha '{NOME_PLANILHA_INSCRICOES}' lida.")
        except Exception as e:
            log_mensagens_internas.append(f"[FALHA] Ao ler planilha de inscrições: {e}")
            log_mensagens_internas.append("        >> Verifique se a conta de serviço tem permissão de EDITOR nesta planilha! <<")
            raise

        # 3. Filtra equipes aprovadas, etc.
        df_confirm = df_confirm[df_confirm['Equipe aprovada?'] == 'Aprovada'].copy()
        df_confirm['Equipe_norm'] = df_confirm['Equipe'].apply(normalize_text)
        df_com_nomes = df_confirm[df_confirm['Equipe_norm'] != ''].copy()
        if not df_com_nomes[df_com_nomes['Equipe_norm'].duplicated(keep=False)].empty:
            raise Exception("Duplicatas encontradas nas inscrições.")
        log_mensagens_internas.append("[INFO] Inscrições validadas.")

        # 4. Normaliza e transforma os dados do Kaggle
        df_all["TeamMemberUserNames"] = df_all["TeamMemberUserNames"].apply(safe_process_team_members)
        df_all['TeamName_norm'] = df_all['TeamName'].apply(normalize_text)
        df_pivot = df_all.pivot_table(index='TeamName_norm', columns='competition', values='Score', aggfunc='sum').reset_index()
        df_info_equipes = df_all[['TeamName_norm', 'TeamName', 'TeamMemberUserNames']].drop_duplicates(subset=['TeamName_norm'])
        df_pivot = pd.merge(df_pivot, df_info_equipes, on='TeamName_norm', how='left')
        df_pivot.columns.name = None
        for comp in competitions:
            if comp not in df_pivot.columns: df_pivot[comp] = 0
        df_pivot.fillna(0, inplace=True)
        score_cols = [c for c in competitions if c in df_pivot.columns]
        df_pivot['TotalScore'] = df_pivot[score_cols].sum(axis=1)

        # 5. Cruza os dados
        df_ranked = pd.merge(df_pivot, df_confirm[['Equipe_norm', 'Escola']], left_on='TeamName_norm', right_on='Equipe_norm', how='left')

        equipes_nao_encontradas = df_ranked[df_ranked['Escola'].isnull()]
        if not equipes_nao_encontradas.empty:
            log_mensagens_internas.append("\n[AVISO] Equipes do Kaggle não encontradas:")
            for _, row in equipes_nao_encontradas.iterrows():
                log_mensagens_internas.append(f"  - {row['TeamName']}")

        df_ranked.dropna(subset=['Escola'], inplace=True)
        log_mensagens_internas.append("[INFO] Dados cruzados.")

        # 6. Separa e 7. Ordena
        df_publica = df_ranked[df_ranked['Escola'] == 'Escola Pública'].sort_values("TotalScore", ascending=False).reset_index(drop=True)
        df_publica["Rank"] = df_publica.index + 1
        df_privada = df_ranked[df_ranked['Escola'] == 'Escola Privada'].sort_values("TotalScore", ascending=False).reset_index(drop=True)
        df_privada["Rank"] = df_privada.index + 1
        log_mensagens_internas.append("[INFO] Rankings gerados.")


# ALTERAR AQUI O NOME DOS DESAFIOS CONFORME NECESSÁRIO!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        # 8. Prepara DFs Finais
        colunas_finais = {
            'Rank': 'Rank', 'TeamName': 'Nome da Equipe', 'TotalScore': 'Pontuação Total',
            'aprendizado-de-maquina-2-fase': 'Score AM',
            'visao-computacional-2-fase': 'Score VC',
            'linguagem-natural-2-fase': 'Score PLN',
            'TeamMemberUserNames': 'Membros' #N é utilizada no display, mas mantida para possível uso futuro
        }
        colunas_para_mostrar = list(colunas_finais.keys())
        df_publica_final = df_publica[[col for col in colunas_para_mostrar if col in df_publica.columns]].copy()
        df_publica_final.rename(columns=colunas_finais, inplace=True)
        df_privada_final = df_privada[[col for col in colunas_para_mostrar if col in df_privada.columns]].copy()
        df_privada_final.rename(columns=colunas_finais, inplace=True)

        # Tenta escrever no Google Sheets
        try:
            spreadsheet = gc.open(NOME_DA_PLANILHA_SAIDA)
            write_to_sheet_overwrite(spreadsheet, "Ranking Escolas Públicas", df_publica_final, log_mensagens_internas)
            write_to_sheet_overwrite(spreadsheet, "Ranking Escolas Privadas", df_privada_final, log_mensagens_internas)
            append_to_backup_sheet(spreadsheet, "Backup Escolas Públicas", df_publica_final, log_mensagens_internas)
            append_to_backup_sheet(spreadsheet, "Backup Escolas Privadas", df_privada_final, log_mensagens_internas)
            log_mensagens_internas.append("[INFO] Google Sheets atualizado.")
        except Exception as e:
            log_mensagens_internas.append(f"[AVISO] Erro ao escrever no Google Sheets: {e}")
            log_mensagens_internas.append("        >> Verifique se a conta de serviço tem permissão de EDITOR nesta planilha! <<")
            if status_final_interno == "[SUCESSO]": status_final_interno = "[AVISO]"

    except SystemExit as e:
        status_final_interno = "[AVISO]"
        log_mensagens_internas.append(f"[AVISO] Fluxo interrompido: {e}")
        return df_publica_final, df_privada_final, status_final_interno, datetime.now(BR_TZ)

    except Exception as e:
        status_final_interno = "[FALHA]"
        log_mensagens_internas.append(f"\n[FALHA CRÍTICA] Erro no pipeline: {e}\n")

    finally:
        if any("[AVISO]" in msg for msg in log_mensagens_internas) and status_final_interno != "[FALHA]":
            status_final_interno = "[AVISO]"

    print("\n".join(log_mensagens_internas))

    return df_publica_final, df_privada_final, status_final_interno, datetime.now(BR_TZ)

# --- FRONTEND (HTML/CSS/JS FINAL) ---
# (Esta parte do código permanece exatamente a mesma da sua versão)

st.set_page_config(page_title="Leaderboard Olimpíada IA 2025", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh") # 5 minutos

with st.spinner("Buscando os dados mais recentes..."):
    df_pub, df_priv, status, data_exec = buscar_e_processar_dados()

json_publica_str = df_pub.to_json(orient='records', force_ascii=False)
json_privada_str = df_priv.to_json(orient='records', force_ascii=False)

if "[FALHA]" in status: status_class = "FAILURE"; status_text = "FALHA NA ATUALIZAÇÃO"
elif "[AVISO]" in status: status_class = "WARNING"; status_text = "ATUALIZADO COM AVISOS"
else: status_class = "SUCCESS"; status_text = "ATUALIZADO COM SUCESSO"

# --- CSS FINAL ---
CSS_STRING = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Roboto:wght@400;700&display=swap');
    @import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css');
    :root {
        --cor-fundo-geral-leaderboard: #FFFFFF; --cor-fundo-container: #F0F4FF; --cor-card: #FFFFFF;
        --cor-card-titulo: rgba(0, 0, 204, 0.05); --cor-fonte: #0000CC; --cor-fonte-secundaria: #00008B;
        --cor-status-bar-principal: #E65100; --cor-status-text: #FFFFFF; --cor-destaque: #E6007A;
        --cor-ouro: #FFD700; --cor-prata: #C0C0C0; --cor-bronze: #CD7F32;
        --fonte-pixel: 'Press Start 2P', cursive; --fonte-corpo: 'Roboto', sans-serif;
    }
    body { background-color: var(--cor-fundo-geral-leaderboard) !important; margin: 0; padding: 0; }
    .colab-leaderboard-container { font-family: var(--fonte-corpo); background-color: var(--cor-fundo-geral-leaderboard);
        color: var(--cor-fonte); margin: 0; padding: 20px; border-radius: 12px; box-sizing: border-box; position: relative; }
    header { text-align: center; margin-bottom: 30px; }
    header h1 { font-family: var(--fonte-pixel); color: var(--cor-fonte); font-size: 1.8rem; text-shadow: 2px 2px 4px rgba(0,0,0,0.1); margin-bottom: 15px; }
    .status-bar { font-family: var(--fonte-pixel); font-size: 0.8rem; color: var(--cor-status-text); background-color: var(--cor-status-bar-principal);
        margin-top: 15px; padding: 10px; border-radius: 5px; line-height: 1.5; box-shadow: 0 2px 8px rgba(0,0,0,0.3); }
    .status-SUCCESS { background-color: #4CAF50 !important; color: #FFFFFF !important; }
    .status-WARNING { background-color: #FFC107 !important; color: #000000 !important; }
    .status-FAILURE { background-color: #F44336 !important; color: #FFFFFF !important; }
    .container { display: flex; flex-wrap: wrap; justify-content: space-around; gap: 20px; background-color: var(--cor-fundo-container);
        padding: 20px; border-radius: 8px; border: 2px solid var(--cor-fonte); }
    .leaderboard-card { background-color: var(--cor-card); border-radius: 12px; box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        width: 100%; max-width: 600px; overflow: hidden; flex-grow: 1; border: 4px solid var(--cor-fonte); }
    .leaderboard-card h2 { font-family: var(--fonte-pixel); font-size: 1.1rem; background-color: var(--cor-card-titulo);
        padding: 20px; margin: 0; text-align: center; color: var(--cor-fonte); text-shadow: none; }
    .equipe-item { display: flex; align-items: center; justify-content: space-between; padding: 15px 10px; margin-bottom: 10px;
        border-radius: 8px; background: var(--cor-card); border-left: 7px solid var(--cor-fonte); animation: fadeIn 0.5s ease forwards;
        opacity: 0; border: 2px solid var(--cor-fonte); }
    @keyframes fadeIn { from { opacity: 0; transform: translateX(-20px); } to { opacity: 1; transform: translateX(0); } }
    .equipe-item.rank-1 .rank { color: var(--cor-ouro); }
    .equipe-item.rank-2 .rank { color: var(--cor-prata); }
    .equipe-item.rank-3 .rank { color: var(--cor-bronze); }
    .equipe-item .rank { font-family: var(--fonte-pixel); font-size: 1.3rem; min-width: 45px; text-align: center; color: var(--cor-fonte); }
    .equipe-info { flex-grow: 1; margin-left: 15px; overflow: hidden; }
    .equipe-info .nome { font-family: var(--fonte-pixel); font-size: 1.0rem; font-weight: normal; color: var(--cor-fonte); }
    .equipe-score { font-family: var(--fonte-pixel); font-size: 1.2rem; color: var(--cor-destaque); text-align: right; padding-left: 10px; }
    .loading { text-align: center; font-size: 1.2rem; padding: 20px; color: var(--cor-fonte); }
    #fullscreen-btn-container { position: absolute; top: 15px; right: 20px; z-index: 100; }
    #fullscreen-btn { background: rgba(0, 0, 204, 0.1); border: 1px solid var(--cor-fonte); color: var(--cor-fonte); padding: 8px 12px;
        border-radius: 5px; cursor: pointer; font-size: 1.1rem; transition: all 0.2s ease; }
    #fullscreen-btn:hover { background: var(--cor-fonte); color: var(--cor-fundo-geral-leaderboard); box-shadow: 0 0 10px var(--cor-fonte); }
    .colab-leaderboard-container.fake-fullscreen { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 9999;
        overflow-y: auto; margin: 0; border-radius: 0; }
    .colab-leaderboard-container.fake-fullscreen h1 { font-size: 2.5rem; }
</style>
"""

# --- JS FINAL ---
JS_STRING = """
<script>
    function construirHtmlEquipe(equipe, index) {
        const rankClass = `rank-${equipe.Rank}`;
        let rankDisplay;
        switch (equipe.Rank) {
            case 1: rankDisplay = '<i class="fas fa-medal" style="color:var(--cor-ouro)"></i>'; break;
            case 2: rankDisplay = '<i class="fas fa-medal" style="color:var(--cor-prata)"></i>'; break;
            case 3: rankDisplay = '<i class="fas fa-medal" style="color:var(--cor-bronze)"></i>'; break;
            default: rankDisplay = equipe.Rank;
        }
        const pontuacao = parseFloat(equipe['Pontuação Total']).toLocaleString('pt-BR', { maximumFractionDigits: 4 });
        const animacaoDelay = `animation-delay: ${index * 0.05}s;`;
        return `
            <div class="equipe-item ${rankClass}" style="${animacaoDelay}">
                <div class="rank">${rankDisplay}</div>
                <div class="equipe-info">
                    <div class="nome" title="${equipe['Nome da Equipe']}">${equipe['Nome da Equipe']}</div>
                </div>
                <div class="equipe-score">${pontuacao}</div>
            </div>
        `;
    }

    function construirLeaderboards() {
        const leaderboards = [
            { data: window.rankingPublica, elementId: 'leaderboard-publica' },
            { data: window.rankingPrivada, elementId: 'leaderboard-privada' }
        ];
        for (const lb of leaderboards) {
            const container = document.getElementById(lb.elementId);
            if (!lb.data) {
                 container.innerHTML = `<div class="loading">Erro ao carregar dados.</div>`;
                 continue;
            }
            if (lb.data.length === 0) {
                container.innerHTML = `<div class="loading">Nenhuma equipe pontuou ainda.</div>`;
                continue;
            }
            let html = '';
            lb.data.forEach((equipe, index) => {
                html += construirHtmlEquipe(equipe, index);
            });
            container.innerHTML = html;
        }
    }

    function iniciarTimer() {
        let tempoRestante = window.TEMPO_ESPERA;
        const countdownElement = document.getElementById('countdown');
        if (!countdownElement) return;
        const intervalId = setInterval(() => {
            tempoRestante--;
            countdownElement.textContent = `${tempoRestante}s`;
            if (tempoRestante <= 0) {
                clearInterval(intervalId);
                countdownElement.textContent = "Atualizando...";
            }
        }, 1000);
    }

    function setupFullscreenButton() {
        const btn = document.getElementById('fullscreen-btn');
        const icon = btn.querySelector('i');
        const elem = document.querySelector('.colab-leaderboard-container');
        if (!btn || !elem) return;
        btn.addEventListener('click', () => {
            elem.classList.toggle('fake-fullscreen');
            if (elem.classList.contains('fake-fullscreen')) {
                icon.classList.remove('fa-expand');
                icon.classList.add('fa-compress');
                btn.title = "Sair da Tela Cheia";
            } else {
                icon.classList.remove('fa-compress');
                icon.classList.add('fa-expand');
                btn.title = "Ativar Tela Cheia";
            }
        });
    }

    try {
        construirLeaderboards();
        iniciarTimer();
        setupFullscreenButton();
    } catch (e) {
        const errorMsg = '<div class="loading">Erro no JS: ' + e.message + '</div>';
        try { // Tenta mostrar o erro nos containers
           document.getElementById('leaderboard-publica').innerHTML = errorMsg;
           document.getElementById('leaderboard-privada').innerHTML = errorMsg;
        } catch {} // Ignora se os containers não existirem
        console.error(e);
    }
</script>
"""

# --- HTML FINAL ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    {css}
</head>
<body>
    <div class="colab-leaderboard-container">
        <div id="fullscreen-btn-container">
            <button id="fullscreen-btn" title="Ativar Tela Cheia">
                <i class="fas fa-expand"></i>
            </button>
        </div>

        <header>
            <h1>OLIMPÍADA DE<br>INTELIGÊNCIA ARTIFICIAL APLICADA 2025</h1>
            <div class="status-bar status-{status_classe}"> Última atualização: {data_atualizacao} ({status_texto})
                <br>
                Próxima atualização em <span id="countdown">{tempo_espera}</span>s
            </div>
        </header>

        <main class="container">
            <section class="leaderboard-card">
                <h2>RANKING<br>ESCOLAS PÚBLICAS</h2>
                <div id="leaderboard-publica" class="leaderboard-body">
                    <div class="loading">Carregando dados...</div>
                </div>
            </section>

            <section class="leaderboard-card">
                <h2>RANKING<br>ESCOLAS PRIVADAS</h2>
                <div id="leaderboard-privada" class="leaderboard-body">
                    <div class="loading">Carregando dados...</div>
                </div>
            </section>
        </main>

        <script>
            window.rankingPublica = {json_publica};
            window.rankingPrivada = {json_privada};
            window.TEMPO_ESPERA = {tempo_espera};
        </script>
        {js}
    </div>
</body>
</html>
"""

# ----------------------------------------------------------------------------

html_final = HTML_TEMPLATE.format(
    css=CSS_STRING,
    js=JS_STRING,
    status_classe=status_class,
    status_texto=status_text,
    data_atualizacao=data_exec.strftime('%d/%m/%Y %H:%M:%S'),
    tempo_espera=300, # O tempo de refresh (5 min)
    json_publica=json_publica_str,
    json_privada=json_privada_str
)

# Exibir o HTML no Streamlit
# Aumentamos a altura padrão para acomodar listas mais longas sem scroll inicial
components.html(html_final, height=1500, scrolling=True) # A altura agora será dinâmica

# Botão para forçar atualização
if st.button("Forçar Atualização Agora"):
    st.cache_data.clear()
    st.rerun()