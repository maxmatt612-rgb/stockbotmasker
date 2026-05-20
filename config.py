import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

DEFAULT_WATCHLIST = [
    "PLTR", "SOFI", "F", "NIO", "RIVN",
    "NOK", "SNAP", "LCID", "VALE", "XPEV",
]

REPORT_HOUR = 7
REPORT_MINUTE = 30
ALERT_CHECK_INTERVAL = 300

# ~250 azioni disponibili su Revolut tipicamente sotto i $20
REVOLUT_UNIVERSE = [
    # ── Crypto Mining ──
    "MARA", "RIOT", "CLSK", "HUT", "BTBT", "CIFR", "IREN", "CORZ", "BITF", "WULF", "HIVE",
    # ── EV & Mobilità Elettrica ──
    "NIO", "RIVN", "LCID", "XPEV", "LI", "NKLA", "GOEV", "SOLO", "HYLN", "ZEV",
    "MVST", "PTRA", "FREYR", "AYRO", "WKHS",
    # ── Clean Energy ──
    "PLUG", "FCEL", "BLNK", "CHPT", "BE", "GEVO", "SUNW", "FLUX",
    # ── Auto ──
    "F",
    # ── Tech & Software ──
    "PLTR", "SOFI", "BB", "NOK", "SNAP", "SIRI", "LUMN", "GPRO", "MVIS",
    "SKLZ", "OPEN", "TIGR", "KPLT", "BARK", "GENI", "OTRK",
    "UWMC", "RKT", "PAYO", "DAVE", "CURO", "NRDS", "MAPS",
    "GFAI", "MULN", "FFIE", "KOSS", "BBIG",
    # ── Finance internazionale ──
    "ITUB", "PBR", "BBD", "SID",
    # ── Metalli Preziosi & Mining ──
    "VALE", "BTG", "KGC", "HL", "AG", "EGO", "PAAS", "CDE", "FSM",
    "SBSW", "DRD", "GORO", "AUY", "HYMC",
    # ── Airlines ──
    "AAL", "JBLU", "HA", "SAVE",
    # ── Cruise & Travel ──
    "CCL", "NCLH",
    # ── Cannabis ──
    "TLRY", "SNDL", "CGC", "ACB", "OGI", "CRON", "GRWG",
    # ── Biotech & Pharma ──
    "NVAX", "OCGN", "SENS", "ACMR", "SAVA", "AGEN", "ADMA", "MNKD",
    "NKTR", "PGEN", "SEER", "VKTX", "LXRX", "FREQ", "CTIC",
    "IMMP", "JAGX", "BLPH", "TBPH", "XENE", "ALDX", "AMRN",
    "ARDX", "ATRS", "AVXL", "BCRX", "BHVN", "RXRX", "ALEC",
    "ANAB", "FATE", "EDIT", "NTLA", "TWST", "BCAB",
    # ── Retail & Entertainment ──
    "AMC", "CLOV", "EXPR",
    # ── Space ──
    "SPCE", "ASTR", "RDW",
    # ── Energy & Oil ──
    "RIG", "TELL", "BORR", "INDO", "REI", "SWN", "HPK",
    "VAALCO", "BATL", "ROCC", "PHX", "USWS",
    # ── Shipping ──
    "CTRM", "TOPS", "GOGL", "SBLK", "EGLE", "FREE", "NMM",
    # ── China ADR ──
    "GRAB", "GOTU", "TUYA", "KC", "DOYU", "HUYA", "IQ",
    # ── Telecomunicazioni ──
    "LBTYB", "IDT",
    # ── BDC / Lending ──
    "PSEC", "GAIN", "TPVG", "SLRC", "FDUS", "TRIN", "ORCC",
    # ── Varie ──
    "DNA", "BNGO", "PACB", "STEM", "VIEW",
    "MOXC", "MEGL", "SPRT", "GME",
    # ── Metaverso / Gaming ──
    "GMBL", "GMVD", "HOFV", "NERD",
    # ── Biotech extra ──
    "ACST", "AGIO", "AKBA", "ALKT", "ALLK", "ALNA", "ALNY",
    "ALRN", "ALXO", "AMAG", "AMRX", "ANPC", "APDN", "APLS",
    "APRE", "APTO", "APTX", "ARCT", "ARGT", "ARNA",
    # ── SPAC / Misc ──
    "TCAC", "LOKB", "VCNX", "HZON",
    # ── Penny stocks con volume ──
    "IDEX", "SHIP", "NAKD", "CTRM",
]
