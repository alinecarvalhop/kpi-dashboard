"""
update_kpi_dashboard.py
Atualiza o painel comparativo_kpis_time_alicarvalho_part1.html com dados do BigQuery.
Executa diariamente via agente agendado Claude Code.

Dependências: pip install google-cloud-bigquery
Autenticação:  gcloud auth application-default login
"""

import re
import json
from datetime import datetime, date
from pathlib import Path
from google.cloud import bigquery

# ── CONFIGURAÇÃO ───────────────────────────────────────────────────────────────
PROJECT     = "meli-bi-data"
DATASET     = "WHOWNER"
HTML_PATH   = Path(__file__).parent / "comparativo_kpis_time_alicarvalho_part1.html"
TL_USER     = "alicarvalho"
SITE_ID     = "MLB"
QUEUE_IDS   = ("HSP_Publicaciones_Sellers_Mature", "BR_Publicaciones_Sellers_Mature")

ALL_REPS = [
    "aaugustinho","cafarias","lianadsilva","losouza","lyalves",
    "marguedes","marlaraujo","phegoncalves","stdiniz","uizfsilva","vfarias",
]

MES_MAP = {1:"jan", 2:"feb", 3:"mar", 4:"apr", 5:"mai", 6:"jun",
           7:"jul", 8:"aug", 9:"sep", 10:"oct", 11:"nov", 12:"dec"}

client = bigquery.Client(project=PROJECT)

# ── QUERIES ────────────────────────────────────────────────────────────────────

SQL_CORE = f"""
-- NPS Linear, NPS Colaborativo, Estilo, TMO, Produtividade, TDI
-- Tabela: {DATASET}.DM_CX_DISCOVER_REP_METRICS
SELECT
    LOWER(REP_NICK)                          AS rep,
    EXTRACT(MONTH FROM DS_DATE)              AS mes,
    ROUND(AVG(NPS_LINEAL_SCORE), 1)          AS nps_lineal,
    ROUND(AVG(NPS_COLAB_SCORE), 1)           AS nps_colab,
    ROUND(AVG(ESTILO_MELI_SCORE), 1)         AS estilo,
    ROUND(AVG(TMO_OFFLINE_MIN), 1)           AS tmo,
    ROUND(AVG(PRODUCTIVITY_CASES_PER_HOUR), 2) AS prod,
    ROUND(AVG(TDI_RATE * 100), 1)            AS tdi
FROM `{PROJECT}.{DATASET}.DM_CX_DISCOVER_REP_METRICS`
WHERE
    EXTRACT(YEAR FROM DS_DATE) = 2026
    AND SITE_ID = '{SITE_ID}'
    AND QUEUE_ID IN {QUEUE_IDS}
    AND LOWER(TL_NICK) = '{TL_USER}'
GROUP BY 1, 2
ORDER BY 1, 2
"""

SQL_STATUS = f"""
-- Tempos Online, Break, Falha, Coaching — percentual e horas
-- Tabela: {DATASET}.BT_FACT_STATUS_LOGS_REP_SFTP
SELECT
    LOWER(REP_NICK)                                          AS rep,
    EXTRACT(MONTH FROM DS_DATE)                              AS mes,
    ROUND(SUM(ONLINE_HOURS + AJUDAS_HOURS), 2)               AS t_online_h,
    ROUND(SUM(BREAK_HOURS), 2)                               AS t_break_h,
    ROUND(SUM(FALHA_HOURS), 2)                               AS t_falha_h,
    ROUND(SUM(COACHING_HOURS), 2)                            AS t_coaching_h,
    ROUND(SUM(LOGGED_HOURS), 2)                              AS t_logado_h,
    ROUND(SAFE_DIVIDE(SUM(ONLINE_HOURS + AJUDAS_HOURS),
                      SUM(LOGGED_HOURS)) * 100, 1)           AS pct_online,
    ROUND(SAFE_DIVIDE(SUM(BREAK_HOURS),
                      SUM(LOGGED_HOURS)) * 100, 1)           AS pct_break,
    ROUND(SAFE_DIVIDE(SUM(FALHA_HOURS),
                      SUM(LOGGED_HOURS)) * 100, 1)           AS pct_falha,
    ROUND(SAFE_DIVIDE(SUM(COACHING_HOURS),
                      SUM(LOGGED_HOURS)) * 100, 1)           AS pct_coaching
FROM `{PROJECT}.{DATASET}.BT_FACT_STATUS_LOGS_REP_SFTP`
WHERE
    EXTRACT(YEAR FROM DS_DATE) = 2026
    AND SITE_ID = '{SITE_ID}'
    AND LOWER(TL_NICK) = '{TL_USER}'
GROUP BY 1, 2
HAVING SUM(LOGGED_HOURS) > 5   -- exclui meses com < 5h logado
ORDER BY 1, 2
"""

SQL_ESTILO_MOM = f"""
-- Estilo por Momentos (Início, Exploração, Guia, Fechamento, Como)
-- Tabela: {DATASET}.DM_CX_QM_METRIC_ESTILO_QS_REPORTING
SELECT
    LOWER(REP_NICK)                                  AS rep,
    EXTRACT(MONTH FROM DS_DATE)                      AS mes,
    ROUND(AVG(MOMENTO_1_INICIO_CONTATO_SCORE), 1)   AS inicio,
    ROUND(AVG(MOMENTO_2_EXPLORACAO_SCORE), 1)        AS exp,
    ROUND(AVG(MOMENTO_3_GUIA_ASSESSOR_SCORE), 1)     AS guia,
    ROUND(AVG(MOMENTO_4_FECHAMENTO_SCORE), 1)        AS fecha,
    ROUND(AVG(MOMENTO_5_COMO_SCORE), 1)              AS como,
    ROUND(AVG(ESTILO_TOTAL_SCORE), 1)                AS total,
    COUNT(*)                                          AS casos
FROM `{PROJECT}.{DATASET}.DM_CX_QM_METRIC_ESTILO_QS_REPORTING`
WHERE
    EXTRACT(YEAR FROM DS_DATE) = 2026
    AND SITE_ID = '{SITE_ID}'
    AND LOWER(TL_NICK) = '{TL_USER}'
GROUP BY 1, 2
ORDER BY 1, 2
"""

# ── HELPERS ────────────────────────────────────────────────────────────────────

def query_to_dict(sql, key_fields):
    """Executa query e retorna dict aninhado: {rep: {mes_str: {campo: valor}}}"""
    result = {}
    for row in client.query(sql).result():
        rep = row["rep"]
        mes = MES_MAP.get(row["mes"])
        if not mes or rep not in ALL_REPS:
            continue
        if rep not in result:
            result[rep] = {}
        result[rep][mes] = {f: row[f] for f in key_fields}
    return result

def build_flat(data_dict, field):
    """Constrói {rep: {mes: valor}} para um único campo."""
    out = {}
    for rep in ALL_REPS:
        out[rep] = {}
        for mes in MES_MAP.values():
            val = data_dict.get(rep, {}).get(mes, {}).get(field)
            out[rep][mes] = val
    return out

def to_js_obj(d, rep_list, mes_list, fmt=None):
    """Serializa dict Python para literal JS."""
    lines = []
    for rep in rep_list:
        vals = []
        for mes in mes_list:
            v = d.get(rep, {}).get(mes)
            if v is None:
                vals.append("null")
            elif fmt == "float2":
                vals.append(f"{v:.2f}")
            elif fmt == "float1":
                vals.append(f"{v:.1f}")
            else:
                vals.append(str(v) if v is not None else "null")
        inner = ", ".join(f"{m}:{v}" for m, v in zip(mes_list, vals))
        lines.append(f"  {rep}:{{{inner}}}")
    return "{\n" + ",\n".join(lines) + "\n}"

def to_js_estilo_mom(data_dict, rep_list, mes_list):
    """Serializa estiloMom para literal JS."""
    lines = []
    for rep in rep_list:
        mes_entries = []
        for mes in mes_list:
            d = data_dict.get(rep, {}).get(mes)
            if d is None:
                mes_entries.append(f"    {mes}:null")
            else:
                mes_entries.append(
                    f"    {mes}:{{inicio:{d['inicio']},exp:{d['exp']},"
                    f"guia:{d['guia']},fecha:{d['fecha']},como:{d['como']},"
                    f"total:{d['total']},casos:{d['casos']}}}"
                )
        lines.append("  " + rep + ": {\n" + ",\n".join(mes_entries) + ",\n  }")
    return "{\n" + ",\n".join(lines) + "\n}"

def replace_js_var(html, var_name, new_value):
    """Substitui o conteúdo de uma const JS no HTML."""
    pattern = rf"(const {var_name}\s*=\s*)\{{[\s\S]*?\}};"
    replacement = rf"\g<1>{new_value};"
    result, n = re.subn(pattern, replacement, html)
    if n == 0:
        print(f"  AVISO: variável '{var_name}' não encontrada no HTML")
    return result

# ── EXECUÇÃO PRINCIPAL ─────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Iniciando atualização do dashboard...")
    mes_list = list(MES_MAP.values())  # jan..dec (usamos só os disponíveis)

    # 1. Buscar dados
    print("  → Consultando DM_CX_DISCOVER_REP_METRICS...")
    core = query_to_dict(SQL_CORE,
        ["nps_lineal","nps_colab","estilo","tmo","prod","tdi"])

    print("  → Consultando BT_FACT_STATUS_LOGS_REP_SFTP...")
    status = query_to_dict(SQL_STATUS,
        ["t_online_h","t_break_h","t_falha_h","t_coaching_h","t_logado_h",
         "pct_online","pct_break","pct_falha","pct_coaching"])

    print("  → Consultando DM_CX_QM_METRIC_ESTILO_QS_REPORTING...")
    estilo_mom = query_to_dict(SQL_ESTILO_MOM,
        ["inicio","exp","guia","fecha","como","total","casos"])

    # 2. Construir objetos por indicador
    nps_lineal_d  = build_flat(core, "nps_lineal")
    nps_d         = build_flat(core, "nps_colab")
    estilo_d      = build_flat(core, "estilo")
    tmo_d         = build_flat(core, "tmo")
    prod_d        = build_flat(core, "prod")
    tdi_d         = build_flat(core, "tdi")
    t_online_d    = build_flat(status, "pct_online")
    t_break_d     = build_flat(status, "pct_break")
    t_falha_d     = build_flat(status, "pct_falha")
    t_coaching_d  = build_flat(status, "pct_coaching")
    t_logado_h_d  = build_flat(status, "t_logado_h")
    t_online_h_d  = build_flat(status, "t_online_h")
    t_break_h_d   = build_flat(status, "t_break_h")
    t_falha_h_d   = build_flat(status, "t_falha_h")
    t_coaching_h_d= build_flat(status, "t_coaching_h")

    # 3. Ler HTML
    html = HTML_PATH.read_text(encoding="utf-8")

    # 4. Substituir variáveis JS
    print("  → Atualizando variáveis JS no HTML...")
    replacements = [
        ("npsLineal",   to_js_obj(nps_lineal_d,   ALL_REPS, mes_list, "float1")),
        ("nps",         to_js_obj(nps_d,           ALL_REPS, mes_list, "float1")),
        ("estilo",      to_js_obj(estilo_d,        ALL_REPS, mes_list, "float1")),
        ("tmo",         to_js_obj(tmo_d,           ALL_REPS, mes_list, "float1")),
        ("prod",        to_js_obj(prod_d,          ALL_REPS, mes_list, "float2")),
        ("tdi",         to_js_obj(tdi_d,           ALL_REPS, mes_list, "float1")),
        ("tOnline",     to_js_obj(t_online_d,      ALL_REPS, mes_list, "float1")),
        ("tBreak",      to_js_obj(t_break_d,       ALL_REPS, mes_list, "float1")),
        ("tFalha",      to_js_obj(t_falha_d,       ALL_REPS, mes_list, "float1")),
        ("tCoaching",   to_js_obj(t_coaching_d,    ALL_REPS, mes_list, "float1")),
        ("tLogadoH",    to_js_obj(t_logado_h_d,    ALL_REPS, mes_list, "float2")),
        ("tOnlineH",    to_js_obj(t_online_h_d,    ALL_REPS, mes_list, "float2")),
        ("tBreakH",     to_js_obj(t_break_h_d,     ALL_REPS, mes_list, "float2")),
        ("tFalhaH",     to_js_obj(t_falha_h_d,     ALL_REPS, mes_list, "float2")),
        ("tCoachingH",  to_js_obj(t_coaching_h_d,  ALL_REPS, mes_list, "float2")),
    ]
    for var, val in replacements:
        html = replace_js_var(html, var, val)

    # estiloMom — estrutura aninhada própria
    html = replace_js_var(html, "estiloMom",
                          to_js_estilo_mom(estilo_mom, ALL_REPS, mes_list))

    # 5. Atualizar data de última atualização
    today_str = date.today().strftime("%d/%m/%Y")
    html = re.sub(
        r'(<span id="last-update">)[^<]*(</span>)',
        rf'\g<1>{today_str}\g<2>',
        html
    )

    # 6. Detectar meses disponíveis e atualizar MES array + MES_LABEL
    meses_com_dados = sorted(
        {mes for rep_d in core.values() for mes in rep_d.keys()},
        key=lambda m: list(MES_MAP.values()).index(m)
    )
    mes_array = str(meses_com_dados).replace("'", '"').replace(" ", "")
    mes_label_map = {
        "jan":"Jan 2026","feb":"Fev 2026","mar":"Mar 2026","apr":"Abr 2026",
        "mai":"Mai 2026","jun":"Jun 2026","jul":"Jul 2026","aug":"Ago 2026",
        "sep":"Set 2026","oct":"Out 2026","nov":"Nov 2026","dec":"Dez 2026",
    }
    mes_label_js = "{" + ",".join(
        f'{m}:"{mes_label_map[m]}"' for m in meses_com_dados
    ) + "}"
    html = re.sub(r"const MES\s*=\s*\[.*?\];",
                  f"const MES = {mes_array};", html)
    html = re.sub(r"const MES_LABEL\s*=\s*\{.*?\};",
                  f"const MES_LABEL = {mes_label_js};", html)

    # 7. Salvar
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✓ Dashboard atualizado: {HTML_PATH}")
    print(f"  ✓ Meses com dados: {meses_com_dados}")
    print(f"  ✓ Data: {today_str}")

if __name__ == "__main__":
    main()
