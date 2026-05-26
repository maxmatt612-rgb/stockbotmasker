import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Gruppo con topic
_raw_chat = os.getenv("GROUP_CHAT_ID", "")
GROUP_CHAT_ID = int(_raw_chat) if _raw_chat else None
_raw_analisi = os.getenv("TOPIC_ANALISI_ID", "")
TOPIC_ANALISI_ID = int(_raw_analisi) if _raw_analisi else None
_raw_notizie = os.getenv("TOPIC_NOTIZIE_ID", "")
TOPIC_NOTIZIE_ID = int(_raw_notizie) if _raw_notizie else None
_raw_grafico = os.getenv("TOPIC_GRAFICO_ID", "")
TOPIC_GRAFICO_ID = int(_raw_grafico) if _raw_grafico else None

DEFAULT_WATCHLIST = [
    "PLTR", "SOFI", "F", "NIO", "RIVN",
    "NOK", "SNAP", "LCID", "VALE", "XPEV",
]

REPORT_HOUR = 7
REPORT_MINUTE = 30
ALERT_CHECK_INTERVAL = 300

# ~550+ azioni disponibili su Revolut tipicamente sotto i €40 (~$43)
REVOLUT_UNIVERSE = [
    # ── Crypto Mining ──
    "MARA", "RIOT", "CLSK", "HUT", "BTBT", "CIFR", "IREN", "CORZ", "BITF", "WULF", "HIVE",
    # ── EV & Mobilità Elettrica ──
    "NIO", "RIVN", "LCID", "XPEV", "LI", "NKLA", "GOEV", "HYLN",
    "MVST", "WKHS", "FFIE", "NIU", "SOLO",
    # ── Clean Energy ──
    "PLUG", "FCEL", "BLNK", "CHPT", "BE", "GEVO", "SUNW", "NOVA",
    "ARRY", "SHLS", "SEDG", "STEM", "ENVX", "HYZN", "CLNE", "EVGO",
    # ── Auto & Mobilità ──
    "F", "LYFT", "JOBY", "ACHR", "STLA", "ERJ",
    # ── AI / Quantum Computing ──
    "IONQ", "RGTI", "QUBT", "QBTS", "SOUN", "BBAI", "AI", "RNLX",
    "ARQQ", "IQM", "BFLY", "LUNR", "RKLB", "ASTS",
    # ── Space & Difesa ──
    "SPCE", "ASTR", "RDW", "MNTS", "VORB",
    # ── Tech & Software (fascia bassa) ──
    "PLTR", "SOFI", "BB", "NOK", "SNAP", "SIRI", "LUMN", "GPRO", "MVIS",
    "OPEN", "TIGR", "BARK", "GENI", "PAYO", "DAVE", "CURO", "MAPS",
    "GFAI", "MULN", "KOSS", "GME", "ERIC", "STM",
    # ── Tech & Software ($20-40) ──
    "HOOD", "U", "ASAN", "DKNG", "RBLX", "LMND", "BMBL", "FUTU",
    "TDOC", "HIMS", "FUBO", "PARA", "WBD", "DISH", "NWSA",
    "ROOT", "VSCO", "BBWI", "WRBY", "LEVI", "FIGS",
    # ── Social Media & Gaming ──
    "PINS", "SKLZ", "NERD", "EDR", "HUYA", "DOYU",
    # ── Finance / Fintech ──
    "ITUB", "PBR", "BBD", "SID", "UPST", "AFRM",
    "PSEC", "GAIN", "TPVG", "SLRC", "FDUS", "TRIN", "ORCC",
    "UWMC", "RKT", "NRDS", "OPFI", "T", "VZ", "KHC",
    # ── Banche europee (ADR) ──
    "DB", "ING", "SAN", "BBVA", "STM", "VOD",
    # ── Banche/Finance Asiatiche ──
    "SMFG", "MFG", "HDB", "IBN",
    # ── Metalli Preziosi & Mining ──
    "VALE", "BTG", "KGC", "HL", "AG", "EGO", "PAAS", "CDE", "FSM",
    "SBSW", "DRD", "GOLD", "MP", "CNX", "GGB",
    # ── Airlines ──
    "AAL", "JBLU", "HA", "SAVE", "LUV",
    # ── Cruise & Travel ──
    "CCL", "NCLH", "RCL",
    # ── Cannabis ──
    "TLRY", "SNDL", "CGC", "ACB", "OGI", "CRON", "GRWG",
    # ── Biotech & Pharma (penny) ──
    "NVAX", "OCGN", "SENS", "SAVA", "AGEN", "ADMA", "MNKD",
    "NKTR", "PGEN", "VKTX", "LXRX", "CTIC",
    "IMMP", "JAGX", "XENE", "AMRN", "ARDX", "AVXL", "BCRX",
    "ANAB", "EDIT", "NTLA", "RXRX", "ACAD", "FOLD", "BLUE",
    "VTRS", "AMRX", "LXRX", "MNKD", "AGEN",
    # ── Biotech ($20-40) ──
    "PRAX", "HALO", "CERT", "ALHC", "ACMR", "DNMR", "BEAM", "CRBU",
    "SGMO", "GRFS", "VKTX", "RCKT", "FATE",
    # ── Retail & Entertainment ──
    "AMC", "CLOV", "EXPR", "BGFV",
    # ── Energy & Oil ──
    "RIG", "TELL", "BORR", "INDO", "REI", "SWN", "HPK",
    "VAALCO", "PHX", "ZIM", "GSL", "ET",
    # ── Shipping ──
    "CTRM", "TOPS", "GOGL", "SBLK", "EGLE", "NMM",
    # ── China ADR ──
    "GRAB", "GOTU", "TUYA", "KC", "IQ",
    "BEKE", "VNET", "GDS", "FINV", "QFIN",
    # ── Telecomunicazioni ──
    "LBTYB", "IDT",
    # ── Healthcare ──
    "OPCH", "ACCD", "OPRX", "CANO", "GDRX",
    # ── Varie ──
    "DNA", "BNGO", "PACB",
    # ── Biotech extra ──
    "AKBA", "ALKT", "ALNY", "APLS",
    "ARCT", "ARNA", "TWST", "BCAB", "BHVN", "ALEC",
    # ── Penny stocks con volume ──
    "IDEX", "SHIP",
]

# ── Mercati Europei ──────────────────────────────────────────────────────────
EUROPEAN_UNIVERSE = [
    # ── Borsa Italiana — blue chip & mid cap (.MI) ──
    "ENEL.MI",   # Enel — utility energia
    "ENI.MI",    # Eni — oil & gas
    "ISP.MI",    # Intesa Sanpaolo — banca
    "TIT.MI",    # Telecom Italia
    "A2A.MI",    # A2A — multiutility
    "SRG.MI",    # Snam — infrastruttura gas
    "TRN.MI",    # Terna — rete elettrica
    "PST.MI",    # Poste Italiane
    "BAMI.MI",   # Banco BPM
    "BMED.MI",   # Banca Mediolanum
    "MB.MI",     # Mediobanca
    "AZM.MI",    # Azimut Holding — asset management
    "LDO.MI",    # Leonardo — difesa & aerospazio
    "G.MI",      # Generali — assicurazioni
    "PIRC.MI",   # Piaggio
    "BZU.MI",    # Buzzi Unicem — costruzioni
    "BGN.MI",    # Banca Generali
    "CPR.MI",    # Caltagirone Editore
    "MARR.MI",   # MARR — distribuzione alimentare
    "SFER.MI",   # Salvatore Ferragamo
    "SPM.MI",    # Saipem — oil services
    "DBRG.MI",   # De' Longhi
    # ── Francia — Euronext Paris (.PA) ──
    "ORA.PA",    # Orange — telecom
    "VIE.PA",    # Veolia — utility
    "RNO.PA",    # Renault — auto
    "HO.PA",     # Thales — difesa
    # ── Germania — Xetra (.DE) ──
    "DB.DE",     # Deutsche Bank
    "AIXA.DE",   # Aixtron — semiconduttori
    "IFX.DE",    # Infineon — semiconduttori
    # ── Olanda — Euronext Amsterdam (.AS) ──
    "ABN.AS",    # ABN AMRO — banca
    "PHIA.AS",   # Philips — health tech
    # ── Spagna — BME (.MC) ──
    "BBVA.MC",   # BBVA — banca
    "SAN.MC",    # Santander — banca
    "TEF.MC",    # Telefónica
]

# Universo completo = US + Europa
REVOLUT_UNIVERSE = REVOLUT_UNIVERSE + EUROPEAN_UNIVERSE

# ── ETF Universe ─────────────────────────────────────────────────────────────
ETF_UNIVERSE = [
    # ── ARK (tematici innovazione) ──
    "ARKK", "ARKG", "ARKQ", "ARKF",
    # ── AI & Robotica ──
    "BOTZ", "ROBO", "AIQ",
    # ── Cybersecurity ──
    "HACK", "CIBR", "BUG",
    # ── Gold & Metalli ──
    "GDX", "GDXJ", "IAU", "SLV", "COPX", "SILJ",
    # ── Mercati emergenti ──
    "EEM", "EWZ", "FXI", "MCHI", "INDA",
    # ── Cannabis ──
    "MSOS", "YOLO",
    # ── Energia ──
    "GUSH", "DRIP",
    # ── Leveraged / Inverse (alta volatilità) ──
    "SQQQ", "TECS", "LABD", "LABU", "TNA", "FAZ", "UVXY",
    # ── Clean energy ──
    "ICLN", "TAN", "FAN",
    # ── Semi / Tech ──
    "SOXS", "FNGU",
]
