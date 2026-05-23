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

DEFAULT_WATCHLIST = [
    "PLTR", "SOFI", "F", "NIO", "RIVN",
    "NOK", "SNAP", "LCID", "VALE", "XPEV",
]

REPORT_HOUR = 7
REPORT_MINUTE = 30
ALERT_CHECK_INTERVAL = 300

# ~400 azioni disponibili su Revolut tipicamente sotto i €35 (~$40)
REVOLUT_UNIVERSE = [
    # ── Crypto Mining ──
    "MARA", "RIOT", "CLSK", "HUT", "BTBT", "CIFR", "IREN", "CORZ", "BITF", "WULF", "HIVE",
    # ── EV & Mobilità Elettrica ──
    "NIO", "RIVN", "LCID", "XPEV", "LI", "NKLA", "GOEV", "HYLN",
    "MVST", "WKHS", "FFIE",
    # ── Clean Energy ──
    "PLUG", "FCEL", "BLNK", "CHPT", "BE", "GEVO", "SUNW", "NOVA",
    "ARRY", "SHLS", "SEDG", "STEM",
    # ── Auto & Mobilità ──
    "F", "LYFT", "JOBY", "ACHR",
    # ── Tech & Software (fascia bassa) ──
    "PLTR", "SOFI", "BB", "NOK", "SNAP", "SIRI", "LUMN", "GPRO", "MVIS",
    "OPEN", "TIGR", "BARK", "GENI", "PAYO", "DAVE", "CURO", "MAPS",
    "GFAI", "MULN", "KOSS", "GME",
    # ── Tech & Software ($20-40) ──
    "HOOD", "U", "ASAN", "DKNG", "RBLX", "LMND", "BMBL", "FUTU",
    "TDOC", "HIMS", "FUBO", "PARA", "WBD", "DISH", "NWSA",
    "ROOT", "VSCO", "BBWI", "WRBY",
    # ── Finance / Fintech ──
    "ITUB", "PBR", "BBD", "SID", "UPST", "AFRM",
    "PSEC", "GAIN", "TPVG", "SLRC", "FDUS", "TRIN", "ORCC",
    "UWMC", "RKT", "NRDS",
    # ── Metalli Preziosi & Mining ──
    "VALE", "BTG", "KGC", "HL", "AG", "EGO", "PAAS", "CDE", "FSM",
    "SBSW", "DRD", "GOLD", "MP", "CNX",
    # ── Airlines ──
    "AAL", "JBLU", "HA", "SAVE", "UAL",
    # ── Cruise & Travel ──
    "CCL", "NCLH", "RCL",
    # ── Cannabis ──
    "TLRY", "SNDL", "CGC", "ACB", "OGI", "CRON", "GRWG",
    # ── Biotech & Pharma (penny) ──
    "NVAX", "OCGN", "SENS", "SAVA", "AGEN", "ADMA", "MNKD",
    "NKTR", "PGEN", "VKTX", "LXRX", "CTIC",
    "IMMP", "JAGX", "XENE", "AMRN", "ARDX", "AVXL", "BCRX",
    "ANAB", "EDIT", "NTLA", "RXRX", "ACAD", "FOLD", "BLUE",
    # ── Biotech ($20-40) ──
    "PRAX", "HALO", "CERT", "ALHC", "ACMR",
    # ── Retail & Entertainment ──
    "AMC", "CLOV", "EXPR", "BGFV",
    # ── Space ──
    "SPCE", "ASTR", "RDW",
    # ── Energy & Oil ──
    "RIG", "TELL", "BORR", "INDO", "REI", "SWN", "HPK",
    "VAALCO", "PHX", "ZIM", "GSL",
    # ── Shipping ──
    "CTRM", "TOPS", "GOGL", "SBLK", "EGLE", "NMM",
    # ── China ADR ──
    "GRAB", "GOTU", "TUYA", "KC", "DOYU", "HUYA", "IQ",
    "BEKE", "VNET", "GDS",
    # ── Telecomunicazioni ──
    "LBTYB", "IDT", "LUMN",
    # ── Healthcare ──
    "OPCH", "ACCD", "OPRX", "CANO",
    # ── Varie ──
    "DNA", "BNGO", "PACB", "VIEW",
    "SPRT", "NERD", "SKLZ",
    # ── Biotech extra ──
    "AKBA", "ALKT", "ALNY", "AMRX", "APLS",
    "ARCT", "ARNA", "FATE", "TWST", "BCAB", "BHVN", "ALEC",
    # ── Penny stocks con volume ──
    "IDEX", "SHIP",
]
