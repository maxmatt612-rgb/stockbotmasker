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

# Costo round-trip (ingresso+uscita) applicato a ogni trade nel backtest simulato:
# stima prudente di commissioni+spread, in percento. Nessun broker reale è a costo zero.
COST_PCT = 0.10

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

# ── Azioni $60–$100 — copertura ampliata Revolut ────────────────────────────
REVOLUT_UPPER = [
    # ── Blue chip / Large cap sub-$100 ──
    "DIS",    # Walt Disney
    "WMT",    # Walmart
    "BAC",    # Bank of America
    "C",      # Citigroup
    "WFC",    # Wells Fargo
    "KO",     # Coca-Cola
    "PYPL",   # PayPal
    "EBAY",   # eBay
    "INTC",   # Intel
    "MU",     # Micron Technology
    "CSCO",   # Cisco Systems
    "GM",     # General Motors
    "FCX",    # Freeport-McMoRan (rame)
    "NEM",    # Newmont (oro)
    "HPQ",    # HP Inc.
    "HPE",    # Hewlett Packard Enterprise
    "STX",    # Seagate Technology
    "WDC",    # Western Digital
    # ── Tech / SaaS ($40–$100) ──
    "UBER",   # Uber Technologies
    "ROKU",   # Roku
    "TWLO",   # Twilio
    "DOCN",   # DigitalOcean
    "BILL",   # Bill.com
    "PATH",   # UiPath
    "CFLT",   # Confluent
    "ESTC",   # Elastic NV
    "ZI",     # ZoomInfo Technologies
    "GTLB",   # GitLab
    "TOST",   # Toast Inc
    "S",      # SentinelOne
    "NET",    # Cloudflare
    "DDOG",   # Datadog
    "ZS",     # Zscaler
    "CIEN",   # Ciena
    "PSTG",   # Pure Storage
    "NCNO",   # nCino
    "ALTR",   # Altair Engineering
    "FRSH",   # Freshworks
    # ── Semiconduttori ──
    "ON",     # ON Semiconductor
    "SWKS",   # Skyworks Solutions
    "QRVO",   # Qorvo
    "WOLF",   # Wolfspeed
    "COHU",   # Cohu Inc.
    "ACLS",   # Axcelis Technologies
    "POWI",   # Power Integrations
    "DIOD",   # Diodes Incorporated
    # ── Consumer / Retail ──
    "CHWY",   # Chewy
    "ETSY",   # Etsy
    "YELP",   # Yelp
    "CAVA",   # CAVA Group
    "SHAK",   # Shake Shack
    "BJ",     # BJ's Wholesale Club
    "FIVE",   # Five Below
    "SFM",    # Sprouts Farmers Market
    "JACK",   # Jack in the Box
    "CELH",   # Celsius Holdings
    "FIZZ",   # National Beverage
    # ── Finance / Fintech ──
    "NU",     # Nu Holdings (Nubank)
    "LC",     # LendingClub
    "RELY",   # Remitly Global
    "FLYW",   # Flywire
    "STEP",   # StepStone Group
    "ENVA",   # Enova International
    # ── Healthcare / Biotech ($60–$100) ──
    "OSCR",   # Oscar Health
    "HQY",    # HealthEquity
    "PRVA",   # Privia Health
    "GMED",   # Globus Medical
    "ACMR",   # ACM Research
    "CCXI",   # ChemoCentryx
    "INMD",   # InMode Ltd
    "MELI",   # MercadoLibre (ADR Latam)
    # ── Energia ──
    "OXY",    # Occidental Petroleum
    "DVN",    # Devon Energy
    "MTDR",   # Matador Resources
    "AR",     # Antero Resources
    "CIVI",   # Civitas Resources
    "MGY",    # Magnolia Oil & Gas
    "PR",     # Permian Resources
    "CHRD",   # Chord Energy
    # ── Industriali / Metalli ──
    "X",      # U.S. Steel
    "CLF",    # Cleveland-Cliffs
    "MT",     # ArcelorMittal ADR
    "TREX",   # Trex Company
    "STLD",   # Steel Dynamics
    "KTOS",   # Kratos Defense
    "SYM",    # Symbotic
    # ── Airlines ──
    "DAL",    # Delta Air Lines
    "UAL",    # United Airlines
    "ALK",    # Alaska Air
    "SKYW",   # SkyWest
    # ── REITs ──
    "NLY",    # Annaly Capital
    "AGNC",   # AGNC Investment
    "MPW",    # Medical Properties Trust
    "ABR",    # Arbor Realty Trust
    "RITM",   # Rithm Capital
    "LADR",   # Ladder Capital
    # ── Gaming / Intrattenimento ──
    "PENN",   # PENN Entertainment
    "CZR",    # Caesars Entertainment
    "MGM",    # MGM Resorts
    "WYNN",   # Wynn Resorts
    # ── China ADR ($60–$100) ──
    "BABA",   # Alibaba
    "JD",     # JD.com
    "BIDU",   # Baidu
    "NTES",   # NetEase
    "TME",    # Tencent Music
    "TAL",    # TAL Education
    "EDU",    # New Oriental Education
    # ── Media ──
    "NYT",    # New York Times
    "LYV",    # Live Nation Entertainment
    "SEAT",   # Vivid Seats
    # ── Altro ──
    "VST",    # Vistra Energy
    "ACM",    # AECOM
    "FLEX",   # Flex Ltd
    "POST",   # Post Holdings
]

# ── AI Universe — copertura ampliata ────────────────────────────────────────
AI_UNIVERSE = [
    # ── AI Infrastruttura & Data Center ──
    "SMCI",   # Super Micro Computer — server AI leader mondiale
    "VRT",    # Vertiv Holdings — raffreddamento & power AI data center
    "DELL",   # Dell Technologies — server PowerEdge AI
    "POWL",   # Powell Industries — distribuzione energia data center
    "ANET",   # Arista Networks — networking AI data center
    # ── AI Chip Edge / Vision / Sensing ──
    "LSCC",   # Lattice Semiconductor — AI inference edge (low power)
    "AMBA",   # Ambarella — AI vision chip (auto, IoT, sicurezza)
    "CEVA",   # CEVA Inc. — IP chip DSP/AI (royalties)
    "ALGM",   # Allegro MicroSystems — AI power & sensing chips
    "MBLY",   # Mobileye Global — AI guida autonoma
    "MKSI",   # MKS Instruments — AI semiconductor equipment
    "UCTT",   # Ultra Clean Holdings — componenti chip AI
    "AEHR",   # Aehr Test Systems — test wafer chip AI
    "FORM",   # FormFactor — semiconductor testing AI
    # ── AI Software & Workflow Automation ──
    "APPN",   # Appian Corporation — AI low-code workflow
    "PEGA",   # Pegasystems — AI decisioning & automation
    "BRZE",   # Braze Inc. — AI customer engagement
    "ZETA",   # Zeta Global — AI marketing cloud
    "AMPL",   # Amplitude — AI product analytics
    "TASK",   # TaskUs — AI business process outsourcing
    "MTTR",   # Matterport — AI 3D digital twin
    "VEEV",   # Veeva Systems — AI cloud per pharma/life sciences
    "NICE",   # NICE Systems — AI enterprise customer experience
    "TTEC",   # TTEC Holdings — AI CX outsourcing
    # ── AI Advertising & Data ──
    "TTD",    # The Trade Desk — AI programmatic advertising
    "IAS",    # Integral Ad Science — AI brand safety
    "DV",     # DoubleVerify — AI ad verification & fraud
    "MGNI",   # Magnite — AI SSP advertising
    "PUBM",   # PubMatic — AI programmatic supply chain
    # ── AI Cybersecurity ──
    "TENB",   # Tenable Holdings — AI vulnerability management
    "VRNS",   # Varonis Systems — AI data security & governance
    "QLYS",   # Qualys — AI cloud security posture
    "RPD",    # Rapid7 — AI security operations
    # ── AI Healthcare & Drug Discovery ──
    "GH",     # Guardant Health — AI liquid biopsy (cancer)
    "EXAS",   # Exact Sciences — AI multi-cancer screening
    "ILMN",   # Illumina — AI genomics sequencing
    "NTRA",   # Natera — AI genetic testing
    "SDGR",   # Schrödinger — AI molecular simulation (drug design)
    "ABSI",   # Absci Corp — AI generativa per drug design
    "SEER",   # Seer Inc. — AI proteomics discovery
    "CDXS",   # Codexis — AI enzyme engineering
    "NRIX",   # Nurix Therapeutics — AI targeted protein degradation
    # ── AI Autonomous & Mobility ──
    "AUR",    # Aurora Innovation — AI self-driving trucks (Uber spin-off)
    "LAZR",   # Luminar Technologies — AI lidar per automotive
    "ARBE",   # Arbe Robotics — AI radar 4D perception
    "ACVA",   # ACV Auctions — AI auto remarketing
    # ── AI Education & Productivity ──
    "COUR",   # Coursera — AI learning platform
    # ── AI Database / Big Data ──
    "MDB",    # MongoDB — database AI-native (vector search)
]

# ── Azioni $100–$200 — big del mercato ─────────────────────────────────────
REVOLUT_PREMIUM = [
    # ── Mega cap tech ($100–$200) ──
    "NVDA",   # NVIDIA (post-split 10:1, giugno 2024)
    "AMD",    # Advanced Micro Devices
    "GOOGL",  # Alphabet / Google
    "AMZN",   # Amazon
    "AAPL",   # Apple
    "ORCL",   # Oracle
    "QCOM",   # Qualcomm
    "TXN",    # Texas Instruments
    "AMAT",   # Applied Materials
    "TSM",    # Taiwan Semiconductor ADR
    "AVGO",   # Broadcom
    "ARM",    # ARM Holdings
    # ── Tech / SaaS ($100–$200) ──
    "SNOW",   # Snowflake
    "DDOG",   # Datadog
    "DASH",   # DoorDash
    "ABNB",   # Airbnb
    "EXPE",   # Expedia Group
    "EA",     # Electronic Arts
    "TTWO",   # Take-Two Interactive
    "PANW",   # Palo Alto Networks
    "OKTA",   # Okta
    "COIN",   # Coinbase Global
    "ZM",     # Zoom Video
    "CRWD",   # CrowdStrike
    "SPLK",   # Splunk (acquisizione Cisco in corso)
    # ── Semiconduttori ($100–$200) ──
    "MRVL",   # Marvell Technology
    "MPWR",   # Monolithic Power Systems
    "ENTG",   # Entegris
    "ONTO",   # Onto Innovation
    # ── Healthcare / Pharma ($100–$200) ──
    "JNJ",    # Johnson & Johnson
    "ABBV",   # AbbVie
    "BIIB",   # Biogen
    "GILD",   # Gilead Sciences
    "MRK",    # Merck & Co.
    "BMY",    # Bristol-Myers Squibb
    "DXCM",   # Dexcom
    "PODD",   # Insulet Corporation
    # ── Energia ($100–$200) ──
    "XOM",    # ExxonMobil
    "CVX",    # Chevron
    "COP",    # ConocoPhillips
    "EOG",    # EOG Resources
    "VLO",    # Valero Energy
    "MPC",    # Marathon Petroleum
    "PSX",    # Phillips 66
    "WMB",    # Williams Companies
    # ── Finance ($100–$200) ──
    "MS",     # Morgan Stanley
    "COF",    # Capital One Financial
    "IBKR",   # Interactive Brokers
    "BX",     # Blackstone Inc
    "KKR",    # KKR & Co.
    "APO",    # Apollo Global Management
    "FDS",    # FactSet Research
    # ── Consumer ($100–$200) ──
    "TGT",    # Target Corporation
    "ROST",   # Ross Stores
    "TJX",    # TJX Companies
    "SBUX",   # Starbucks (sotto $100 a volte, incluso per sicurezza)
    # ── Difesa / Aerospazio ($100–$200) ──
    "RTX",    # RTX Corporation (Raytheon)
    "SAIC",   # Science Applications International
    "BAH",    # Booz Allen Hamilton
    "HII",    # Huntington Ingalls Industries
    # ── Industriali ($100–$200) ──
    "HON",    # Honeywell International
    "EMR",    # Emerson Electric
    "ROK",    # Rockwell Automation
    "IR",     # Ingersoll Rand
    "XYL",    # Xylem Inc.
    # ── Crypto / Alternative ──
    "MSTR",   # MicroStrategy (Bitcoin proxy, molto volatile)
    "MARA",   # già in lista, skip
]

# Universo completo = US base + $60-100 + $100-200 + AI + Europa
REVOLUT_UNIVERSE = list(dict.fromkeys(
    REVOLUT_UNIVERSE + REVOLUT_UPPER + REVOLUT_PREMIUM + AI_UNIVERSE + EUROPEAN_UNIVERSE
))

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
