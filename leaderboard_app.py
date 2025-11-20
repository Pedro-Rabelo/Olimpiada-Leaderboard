# LEADERBOARD V2: LISTA MANUAL DE EQUIPES + KAGGLE (SEM GOOGLE SHEETS)
# Visual: Branco/Azul/Laranja (Pixel Font)
# Para rodar: streamlit run leaderboard_app.py

import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import zipfile
import unidecode
import os
import shutil
import json
from datetime import datetime
import zoneinfo

# --- CONFIGURAÇÕES ---
KAGGLE_JSON_PATH = "kaggle.json"

# LISTA DE COMPETIÇÕES (Fase 2)
competitions = ["aprendizado-de-maquina-2-fase", "visao-computacional-2-fase", "linguagem-natural-2-fase"]

# LISTA MANUAL DE EQUIPES E CATEGORIAS
# O script vai usar isso para filtrar e categorizar os dados vindos do Kaggle
TEAMS_INFO = {
    # Escolas Públicas
    'Cristal Neural': 'publica',
    'PyLinux': 'publica',
    'CEPMGÊNIOS': 'publica',
    'CyberParty': 'publica',
    'UaiTech': 'publica',
    'uA.I sô': 'publica',
    'Cerrado Neural': 'publica',
    'PYTHON WARRIORS': 'publica',
    'Ohmicros': 'publica',
    'OhmBotsIFG': 'publica',
    'Neurobit IFG': 'publica',
    'IFG GYNBOT ADA LOVELACE': 'publica',
    'IFIpaTech.IA': 'publica',
    'TecnoLíderes': 'publica',
    'B.I.G. MINDS': 'publica',
    
    # Escolas Privadas
    'Stack': 'privada',
    'TechBronx': 'privada',
    'MVP TECH': 'privada',
    'GeniAIs': 'privada',
    'magic bubbles': 'privada',
    'Equipe Raiz': 'privada',
    'Furacão NXT': 'privada',
    'NANOTECH/ROBOCOPE': 'privada',
    'Código Triplo': 'privada',
    'Pequi_Artificial': 'privada',
    '404Bots': 'privada',
    'Next Stage': 'privada',
    'Sexteto Empresarial AI': 'privada',
    'Cognitec': 'privada',
    'EQUIPE PLANETA': 'privada'
}

BR_TZ = zoneinfo.ZoneInfo("America/Sao_Paulo")

# --- FUNÇÕES AUXILIARES ---
def normalize_text(text):
    """Remove acentos e coloca em minúsculas para comparação segura"""
    text = str(text).lower().strip()
    text = unidecode.unidecode(text)
    return text

# Pré-processa a lista de equipes para normalizar as chaves
# Cria um dicionário reverso: { 'cristal neural': {'OriginalName': 'Cristal Neural', 'Type': 'publica'} }
NORMALIZED_TEAMS = {}
for name, category in TEAMS_INFO.items():
    norm_name = normalize_text(name)
    NORMALIZED_TEAMS[norm_name] = {'OriginalName': name, 'Type': category}

# --- AUTENTICAÇÃO KAGGLE ---
try:
    kaggle_dir = os.path.expanduser("~/.kaggle")
    os.makedirs(kaggle_dir, exist_ok=True)
    kaggle_dest_path = os.path.join(kaggle_dir, "kaggle.json")

    # Prioridade: Arquivo local -> Segredos do Streamlit
    if os.path.exists(KAGGLE_JSON_PATH):
        shutil.copy(KAGGLE_JSON_PATH, kaggle_dest_path)
    else:
        try:
            kaggle_credentials = {
                "username": st.secrets["kaggle"]["username"],
                "key": st.secrets["kaggle"]["key"]
            }
            with open(kaggle_dest_path, "w") as f:
                json.dump(kaggle_credentials, f)
        except: pass # Segue tentando importar, vai dar erro lá na frente se falhar
    
    if os.path.exists(kaggle_dest_path):
        os.chmod(kaggle_dest_path, 0o600)
    
    from kaggle import api
    api.authenticate() # Força autenticação para testar

except Exception as e:
    st.error(f"Erro na configuração do Kaggle: {e}")
    st.stop()

# --- FUNÇÃO DE COLETA DE DADOS (Igual ao seu script de teste) ---
@st.cache_data(ttl=60) # Cache de 1 minuto para não bloquear o Kaggle
def get_kaggle_data(competitions_list):
    """
    Baixa os dados de todas as competições listadas e retorna um DataFrame unificado.
    """
    all_dfs = []
    
    for comp in competitions_list:
        zip_path = f"{comp}.zip"
        try:
            # 1. Baixa o ZIP
            api.competition_leaderboard_download(comp, path=".")
            
            # 2. Abre o ZIP e lê o CSV
            if os.path.exists(zip_path):
                with zipfile.ZipFile(zip_path, 'r') as z:
                    # Pega o primeiro arquivo que termina com .csv
                    csv_name = [n for n in z.namelist() if n.endswith('.csv')][0]
                    
                    with z.open(csv_name) as f:
                        df = pd.read_csv(f)
                        df['competition'] = comp # Marca de qual competição veio
                        # Normaliza o nome da equipe no Kaggle para bater com nossa lista
                        df['TeamName_Norm'] = df['TeamName'].apply(normalize_text)
                        all_dfs.append(df)
                
                # Limpeza
                os.remove(zip_path)
                
        except Exception as e:
            print(f"[AVISO] Erro ao processar {comp}: {e}")
            if os.path.exists(zip_path):
                try: os.remove(zip_path)
                except: pass
    
    if not all_dfs:
        return pd.DataFrame()
        
    return pd.concat(all_dfs, ignore_index=True)

# --- PROCESSAMENTO PRINCIPAL ---
def processar_rankings():
    status = "[SUCESSO]"
    timestamp = datetime.now(BR_TZ)
    
    # 1. Busca dados brutos do Kaggle
    df_kaggle = get_kaggle_data(competitions)
    
    # Se não veio nada do Kaggle, retorna vazio mas não quebra
    if df_kaggle.empty:
        return pd.DataFrame(), pd.DataFrame(), "[AVISO]", timestamp

    try:
        # 2. Pivotar os dados (transformar linhas de competição em colunas de score)
        # Mantemos o max score caso a equipe tenha submetido mais de uma vez (improvável no leaderboard público, mas seguro)
        df_pivot = df_kaggle.pivot_table(
            index='TeamName_Norm', 
            columns='competition', 
            values='Score', 
            aggfunc='max'
        ).reset_index()

        # 3. Preencher com 0 onde não tem nota
        for comp in competitions:
            if comp not in df_pivot.columns:
                df_pivot[comp] = 0
        df_pivot.fillna(0, inplace=True)

        # 4. Calcular Total Score
        score_cols = [c for c in competitions if c in df_pivot.columns]
        df_pivot['TotalScore'] = df_pivot[score_cols].sum(axis=1)

        # 5. Cruzar com a nossa lista manual (TEAMS_INFO)
        # Vamos adicionar as colunas 'OriginalName' e 'Type' baseadas no dicionário NORMALIZED_TEAMS
        
        def get_meta_info(norm_name, field):
            if norm_name in NORMALIZED_TEAMS:
                return NORMALIZED_TEAMS[norm_name][field]
            return None

        df_pivot['Nome da Equipe'] = df_pivot['TeamName_Norm'].apply(lambda x: get_meta_info(x, 'OriginalName'))
        df_pivot['Categoria'] = df_pivot['TeamName_Norm'].apply(lambda x: get_meta_info(x, 'Type'))

        # 6. Filtrar apenas as equipes que estão na nossa lista
        # (Removemos quem está no Kaggle mas não está na lista aprovada)
        df_final = df_pivot.dropna(subset=['Nome da Equipe']).copy()

        # 7. Separar Públicas e Privadas e Ordenar
        df_publica = df_final[df_final['Categoria'] == 'publica'].sort_values('TotalScore', ascending=False).reset_index(drop=True)
        df_publica['Rank'] = df_publica.index + 1

        df_privada = df_final[df_final['Categoria'] == 'privada'].sort_values('TotalScore', ascending=False).reset_index(drop=True)
        df_privada['Rank'] = df_privada.index + 1
        
        return df_publica, df_privada, status, timestamp

    except Exception as e:
        print(f"Erro no processamento: {e}")
        return pd.DataFrame(), pd.DataFrame(), "[FALHA]", timestamp

# --- FRONTEND ---
st.set_page_config(page_title="Olimpíada IA 2025", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh")

with st.spinner("Atualizando dados do Kaggle..."):
    df_pub, df_priv, status, date = processar_rankings()

# Converte para JSON para o HTML ler
json_pub = df_pub.to_json(orient='records', force_ascii=False)
json_priv = df_priv.to_json(orient='records', force_ascii=False)

# Define textos de status
if status == "[FALHA]": st_cls, st_txt = "FAILURE", "FALHA TÉCNICA"
elif status == "[AVISO]": st_cls, st_txt = "WARNING", "AGUARDANDO DADOS"
else: st_cls, st_txt = "SUCCESS", "ATUALIZADO COM SUCESSO"

# --- SEU CSS VISUAL (MANTIDO INTACTO) ---
CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Roboto:wght@400;700&display=swap');
    @import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css');

    :root {
        --bg-main: #FFFFFF;
        --bg-container: #F0F4FF;
        --card-bg: #FFFFFF;
        --card-title-bg: rgba(0, 0, 204, 0.05);
        --text-blue: #0000CC;
        --status-orange: #E65100;
        --status-text: #FFFFFF;
        --highlight-pink: #E6007A;
        --gold: #FFD700; --silver: #C0C0C0; --bronze: #CD7F32;
        --font-pixel: 'Press Start 2P', cursive;
    }

    body { background-color: var(--bg-main) !important; margin: 0; padding: 0; }
    
    .colab-leaderboard-container {
        font-family: 'Roboto', sans-serif;
        background-color: var(--bg-main);
        color: var(--text-blue);
        margin: 0; padding: 20px;
        border-radius: 12px;
        position: relative;
    }

    header { text-align: center; margin-bottom: 30px; }
    header h1 {
        font-family: var(--font-pixel);
        color: var(--text-blue);
        font-size: 1.8rem;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
        margin-bottom: 15px;
        line-height: 1.5;
    }
    
    .status-bar {
        font-family: var(--font-pixel);
        font-size: 0.8rem;
        color: var(--status-text);
        background-color: var(--status-orange);
        margin-top: 15px; padding: 12px; border-radius: 5px;
        line-height: 1.5; box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }
    .status-SUCCESS { background-color: #4CAF50 !important; }
    .status-FAILURE { background-color: #F44336 !important; }
    
    .container {
        display: flex; flex-wrap: wrap; justify-content: space-around; gap: 20px;
        background-color: var(--bg-container);
        padding: 20px; border-radius: 8px; border: 2px solid var(--text-blue);
    }

    .leaderboard-card {
        background-color: var(--card-bg);
        border-radius: 12px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        width: 100%; max-width: 600px; 
        flex-grow: 1;
        border: 4px solid var(--text-blue);
        overflow: hidden;
    }

    .leaderboard-card h2 {
        font-family: var(--font-pixel);
        font-size: 1.1rem;
        background-color: var(--card-title-bg);
        padding: 20px; margin: 0; text-align: center;
        color: var(--text-blue); text-shadow: none;
    }

    .leaderboard-body { padding: 20px; }

    .equipe-item {
        display: flex; align-items: center; justify-content: space-between;
        padding: 15px 10px; margin-bottom: 10px;
        border-radius: 8px; background: var(--card-bg);
        border-left: 7px solid var(--text-blue);
        border: 2px solid var(--text-blue);
        animation: fadeIn 0.5s ease forwards; opacity: 0;
    }
    
    @keyframes fadeIn { from { opacity: 0; transform: translateX(-20px); } to { opacity: 1; transform: translateX(0); } }

    .equipe-item.rank-1 .rank { color: var(--gold); }
    .equipe-item.rank-2 .rank { color: var(--silver); }
    .equipe-item.rank-3 .rank { color: var(--bronze); }
    
    .equipe-item .rank {
        font-family: var(--font-pixel);
        font-size: 1.3rem; min-width: 45px; text-align: center;
        color: var(--text-blue);
    }
    
    .equipe-info { flex-grow: 1; margin-left: 15px; overflow: hidden; }
    .equipe-info .nome {
        font-family: var(--font-pixel);
        font-size: 1.0rem;
        font-weight: normal;
        color: var(--text-blue);
    }

    .equipe-score {
        font-family: var(--font-pixel);
        font-size: 1.2rem;
        color: var(--highlight-pink);
        text-align: right; padding-left: 10px;
    }

    .loading { text-align: center; font-size: 1.2rem; padding: 20px; color: var(--text-blue); font-family: var(--font-pixel); }

    #fullscreen-btn-container { position: absolute; top: 15px; right: 20px; z-index: 100; }
    #fullscreen-btn {
        background: rgba(0, 0, 204, 0.1); border: 1px solid var(--text-blue);
        color: var(--text-blue); padding: 8px 12px; border-radius: 5px;
        cursor: pointer; font-size: 1.1rem; transition: all 0.2s ease;
    }
    #fullscreen-btn:hover { background: var(--text-blue); color: #FFFFFF; }
    
    .colab-leaderboard-container.fake-fullscreen {
        position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 9999;
        overflow-y: auto; margin: 0; border-radius: 0;
    }
    .colab-leaderboard-container.fake-fullscreen h1 { font-size: 2.5rem; }
</style>
"""

# --- SEU JS (MANTIDO INTACTO) ---
JS = """
<script>
    function construirHtmlEquipe(equipe, index) {
        const rankClass = `rank-${equipe.Rank}`;
        let rankDisplay;
        switch (equipe.Rank) {
            case 1: rankDisplay = '<i class="fas fa-medal" style="color:var(--gold)"></i>'; break;
            case 2: rankDisplay = '<i class="fas fa-medal" style="color:var(--silver)"></i>'; break;
            case 3: rankDisplay = '<i class="fas fa-medal" style="color:var(--bronze)"></i>'; break;
            default: rankDisplay = equipe.Rank;
        }
        const pontuacao = parseFloat(equipe['TotalScore']).toLocaleString('pt-BR', { maximumFractionDigits: 4 });
        const delay = `animation-delay: ${index * 0.05}s;`;
        return `
            <div class="equipe-item ${rankClass}" style="${delay}">
                <div class="rank">${rankDisplay}</div>
                <div class="equipe-info">
                    <div class="nome">${equipe['Nome da Equipe']}</div>
                </div>
                <div class="equipe-score">${pontuacao}</div>
            </div>
        `;
    }

    function construir(data, id) {
        const el = document.getElementById(id);
        if(!el) return;
        if (!data || data.length === 0) {
             el.innerHTML = `<div class="loading">Nenhuma equipe pontuou ainda.</div>`;
             return;
        }
        let html = '';
        data.forEach((eq, i) => html += construirHtmlEquipe(eq, i));
        el.innerHTML = html;
    }

    function init() {
        construir(window.rankingPublica, 'leaderboard-publica');
        construir(window.rankingPrivada, 'leaderboard-privada');
        
        // Timer
        let time = window.TEMPO_ESPERA;
        const el = document.getElementById('countdown');
        if(el) {
            setInterval(() => {
                time--;
                el.textContent = `${time}s`;
                if(time <= 0) el.textContent = "Atualizando...";
            }, 1000);
        }
        
        // Fullscreen
        const btn = document.getElementById('fullscreen-btn');
        const container = document.querySelector('.colab-leaderboard-container');
        if(btn && container) {
            btn.addEventListener('click', () => {
                container.classList.toggle('fake-fullscreen');
                const icon = btn.querySelector('i');
                if(container.classList.contains('fake-fullscreen')) {
                    icon.classList.remove('fa-expand'); icon.classList.add('fa-compress');
                    btn.title = "Sair";
                } else {
                    icon.classList.remove('fa-compress'); icon.classList.add('fa-expand');
                    btn.title = "Tela Cheia";
                }
            });
        }
    }
    
    setTimeout(init, 100);
</script>
"""

HTML = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8">{CSS}</head>
<body>
    <div class="colab-leaderboard-container">
        <div id="fullscreen-btn-container">
            <button id="fullscreen-btn" title="Ativar Tela Cheia"><i class="fas fa-expand"></i></button>
        </div>
        <header>
            <h1>OLIMPÍADA DE<br>INTELIGÊNCIA ARTIFICIAL APLICADA 2025</h1>
            <div class="status-bar status-{st_cls}"> 
                Última atualização: {date.strftime('%d/%m/%Y %H:%M:%S')} ({st_txt})<br>
                Próxima atualização em <span id="countdown">300</span>s
            </div>
        </header>
        <main class="container">
            <section class="leaderboard-card">
                <h2>RANKING<br>ESCOLAS PÚBLICAS</h2>
                <div id="leaderboard-publica" class="leaderboard-body"><div class="loading">Carregando...</div></div>
            </section>
            <section class="leaderboard-card">
                <h2>RANKING<br>ESCOLAS PRIVADAS</h2>
                <div id="leaderboard-privada" class="leaderboard-body"><div class="loading">Carregando...</div></div>
            </section>
        </main>
        <script>
            window.rankingPublica = {json_pub};
            window.rankingPrivada = {json_priv};
            window.TEMPO_ESPERA = 300;
        </script>
        {JS}
    </div>
</body>
</html>
"""

components.html(HTML, height=1500, scrolling=True)

if st.button("Forçar Atualização Agora"):
    st.cache_data.clear()
    st.rerun()