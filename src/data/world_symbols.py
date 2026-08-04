"""
world_symbols.py
================
Curated REAL large-cap constituents for markets that don't publish a free,
urllib-friendly full listing. These are the major index members
(DAX/CAC/AEX/IBEX/FTSE-MIB/SMI/OMX, HSI, CSI, Nikkei, TSX, ASX, KOSPI, TAIEX,
Ibovespa, JSE Top 40, STI, Tadawul, SET, and others), used to give the universe
real geographic breadth alongside the live full listings (US via NASDAQ Trader,
NSE + BSE via official bhavcopies, LSE).

Coverage spans 34 exchanges across developed and emerging markets. The emerging
half is not decoration: the macro, cross-asset-correlation and currency-strength
modules read "global" conditions off this list, and a universe that stops at
North America, Europe and a thin slice of East Asia produces a regional read
wearing a global label.

Each entry: (yahoo_symbol, name, exchange, currency). The display symbol IS the
yahoo symbol (suffix included) so cross-exchange collisions are impossible
(e.g. SAN.PA Sanofi vs SAN.MC Santander stay distinct). asset_class = equity.

Not exhaustive — real, verifiable names only; no fabricated tickers. That rule
is CHECKED, not merely stated: `scripts/validate_world_symbols.py` prices every
symbol here against the vendor and fails on anything unpriceable or duplicated.
Run it after editing these lists. A mistyped ticker does not crash anything —
the research modules correctly abstain when history will not load — so a dead
symbol becomes a permanently abstaining instrument that nobody investigates,
and enough of them make a market look thin rather than mistyped.
"""

# ── Germany — XETRA (.DE), EUR ────────────────────────────────────────────────
XETRA = [
    ("SAP.DE", "SAP"), ("SIE.DE", "Siemens"), ("ALV.DE", "Allianz"),
    ("DTE.DE", "Deutsche Telekom"), ("MBG.DE", "Mercedes-Benz Group"),
    ("BMW.DE", "BMW"), ("VOW3.DE", "Volkswagen"), ("BAS.DE", "BASF"),
    ("BAYN.DE", "Bayer"), ("DBK.DE", "Deutsche Bank"), ("ADS.DE", "Adidas"),
    ("MUV2.DE", "Munich Re"), ("RWE.DE", "RWE"), ("IFX.DE", "Infineon"),
    ("DHL.DE", "DHL Group"), ("MRK.DE", "Merck KGaA"), ("HEN3.DE", "Henkel"),
    ("DB1.DE", "Deutsche Boerse"), ("VNA.DE", "Vonovia"), ("EOAN.DE", "E.ON"),
    ("FRE.DE", "Fresenius"), ("HEI.DE", "Heidelberg Materials"),
    ("CON.DE", "Continental"), ("ZAL.DE", "Zalando"), ("SHL.DE", "Siemens Healthineers"),
    ("SY1.DE", "Symrise"), ("PAH3.DE", "Porsche SE"), ("P911.DE", "Porsche AG"),
    ("QIA.DE", "Qiagen"), ("BEI.DE", "Beiersdorf"), ("CBK.DE", "Commerzbank"),
    ("ENR.DE", "Siemens Energy"), ("SRT3.DE", "Sartorius"), ("HNR1.DE", "Hannover Re"),
    ("AIR.DE", "Airbus"), ("DTG.DE", "Daimler Truck"), ("RHM.DE", "Rheinmetall"),
]

# ── France — Euronext Paris (.PA), EUR ────────────────────────────────────────
EURONEXT_PA = [
    ("MC.PA", "LVMH"), ("STMPA.PA", "STMicroelectronics"), ("OR.PA", "L'Oreal"), ("URW.PA", "Unibail-Rodamco-Westfield"), ("TTE.PA", "TotalEnergies"),
    ("SAN.PA", "Sanofi"), ("AIR.PA", "Airbus"), ("SU.PA", "Schneider Electric"),
    ("AI.PA", "Air Liquide"), ("EL.PA", "EssilorLuxottica"), ("BNP.PA", "BNP Paribas"),
    ("CS.PA", "AXA"), ("DG.PA", "Vinci"), ("RMS.PA", "Hermes"), ("KER.PA", "Kering"),
    ("SGO.PA", "Saint-Gobain"), ("CAP.PA", "Capgemini"), ("ACA.PA", "Credit Agricole"),
    ("GLE.PA", "Societe Generale"), ("ENGI.PA", "Engie"), ("DSY.PA", "Dassault Systemes"),
    ("STLAP.PA", "Stellantis"), ("ORA.PA", "Orange"), ("VIE.PA", "Veolia"),
    ("PUB.PA", "Publicis"), ("ML.PA", "Michelin"), ("RI.PA", "Pernod Ricard"),
    ("CA.PA", "Carrefour"), ("LR.PA", "Legrand"), ("HO.PA", "Thales"),
    ("EN.PA", "Bouygues"), ("TEP.PA", "Teleperformance"), ("BN.PA", "Danone"),
    ("SAF.PA", "Safran"), ("EDEN.PA", "Edenred"), ("WLN.PA", "Worldline"),
    ("VIV.PA", "Vivendi"), ("SW.PA", "Sodexo"),
]

# ── Netherlands — Euronext Amsterdam (.AS), EUR ───────────────────────────────
EURONEXT_AS = [
    ("ASML.AS", "ASML"), ("APAM.AS", "Aperam"), ("ADYEN.AS", "Adyen"), ("PRX.AS", "Prosus"),
    ("INGA.AS", "ING Group"), ("HEIA.AS", "Heineken"), ("PHIA.AS", "Philips"),
    ("WKL.AS", "Wolters Kluwer"), ("AD.AS", "Ahold Delhaize"), ("ABN.AS", "ABN AMRO"),
    ("AKZA.AS", "Akzo Nobel"), ("KPN.AS", "KPN"), ("RAND.AS", "Randstad"),
    ("ASM.AS", "ASM International"), ("BESI.AS", "BE Semiconductor"),
    ("IMCD.AS", "IMCD"), ("MT.AS", "ArcelorMittal"), ("NN.AS", "NN Group"),
    ("AGN.AS", "Aegon"), ("DSFIR.AS", "DSM-Firmenich"),
]

# ── Italy — Borsa Italiana (.MI), EUR ─────────────────────────────────────────
BORSA_IT = [
    ("ENI.MI", "Eni"), ("ISP.MI", "Intesa Sanpaolo"), ("UCG.MI", "UniCredit"),
    ("ENEL.MI", "Enel"), ("STLAM.MI", "Stellantis"), ("G.MI", "Generali"),
    ("RACE.MI", "Ferrari"), ("TIT.MI", "Telecom Italia"),
    ("PST.MI", "Poste Italiane"), ("SRG.MI", "Snam"), ("TRN.MI", "Terna"),
    ("MB.MI", "Mediobanca"), ("BAMI.MI", "Banco BPM"), ("LDO.MI", "Leonardo"),
    ("MONC.MI", "Moncler"), ("CPR.MI", "Davide Campari"), ("REC.MI", "Recordati"),
    ("A2A.MI", "A2A"), ("BMED.MI", "Banca Mediolanum"), ("PIRC.MI", "Pirelli"),
]

# ── Spain — BME Madrid (.MC), EUR ─────────────────────────────────────────────
BME = [
    ("SAN.MC", "Banco Santander"), ("BBVA.MC", "BBVA"), ("ITX.MC", "Inditex"),
    ("IBE.MC", "Iberdrola"), ("TEF.MC", "Telefonica"), ("REP.MC", "Repsol"),
    ("AENA.MC", "Aena"), ("FER.MC", "Ferrovial"), ("ELE.MC", "Endesa"),
    ("CABK.MC", "CaixaBank"), ("AMS.MC", "Amadeus"), ("NTGY.MC", "Naturgy"),
    ("CLNX.MC", "Cellnex"), ("ACS.MC", "ACS"), ("MAP.MC", "Mapfre"),
    ("ANA.MC", "Acciona"), ("RED.MC", "Redeia"), ("SAB.MC", "Banco Sabadell"),
    ("BKT.MC", "Bankinter"), ("MRL.MC", "Merlin Properties"),
]

# ── Switzerland — SIX (.SW), CHF ──────────────────────────────────────────────
SIX = [
    ("NESN.SW", "Nestle"), ("RO.SW", "Roche"), ("NOVN.SW", "Novartis"),
    ("UBSG.SW", "UBS Group"), ("ZURN.SW", "Zurich Insurance"), ("ABBN.SW", "ABB"),
    ("CFR.SW", "Richemont"), ("LONN.SW", "Lonza"), ("SIKA.SW", "Sika"),
    ("GIVN.SW", "Givaudan"), ("SLHN.SW", "Swiss Life"), ("SREN.SW", "Swiss Re"),
    ("GEBN.SW", "Geberit"), ("HOLN.SW", "Holcim"), ("SCMN.SW", "Swisscom"),
    ("PGHN.SW", "Partners Group"), ("LOGN.SW", "Logitech"), ("SOON.SW", "Sonova"),
    ("ALC.SW", "Alcon"), ("KNIN.SW", "Kuehne + Nagel"),
]

# ── Sweden — Nasdaq Stockholm (.ST), SEK ──────────────────────────────────────
OMX_STO = [
    ("VOLV-B.ST", "Volvo"), ("ERIC-B.ST", "Ericsson"), ("HM-B.ST", "H&M"),
    ("ATCO-A.ST", "Atlas Copco A"), ("ATCO-B.ST", "Atlas Copco B"),
    ("INVE-B.ST", "Investor"), ("SEB-A.ST", "SEB"), ("SWED-A.ST", "Swedbank"),
    ("SHB-A.ST", "Handelsbanken"), ("ASSA-B.ST", "Assa Abloy"), ("SAND.ST", "Sandvik"),
    ("ALFA.ST", "Alfa Laval"), ("TELIA.ST", "Telia"), ("ESSITY-B.ST", "Essity"),
    ("SKF-B.ST", "SKF"), ("HEXA-B.ST", "Hexagon"), ("EVO.ST", "Evolution"),
    ("BOL.ST", "Boliden"), ("NIBE-B.ST", "NIBE"), ("EPI-A.ST", "Epiroc"),
]

# ── Belgium (.BR) / Portugal (.LS) / Finland (.HE) / Norway (.OL) / Denmark (.CO)
EURONEXT_BR = [
    ("ABI.BR", "Anheuser-Busch InBev"), ("KBC.BR", "KBC Group"), ("UCB.BR", "UCB"),
    ("SOLB.BR", "Solvay"), ("GBLB.BR", "Groupe Bruxelles Lambert"), ("PROX.BR", "Proximus"),
    ("COLR.BR", "Colruyt"), ("UMI.BR", "Umicore"), ("AGS.BR", "Ageas"),
    ("MELE.BR", "Melexis"), ("ELI.BR", "Elia"), 
]
EURONEXT_LS = [
    ("EDP.LS", "EDP"), ("GALP.LS", "Galp Energia"), ("JMT.LS", "Jeronimo Martins"),
    ("EDPR.LS", "EDP Renovaveis"), ("NOS.LS", "NOS"), ("SON.LS", "Sonae"),
    ("CTT.LS", "CTT Correios"), ("SEM.LS", "Semapa"), ("NVG.LS", "The Navigator Co"),
]
OMX_HEL = [
    ("NOKIA.HE", "Nokia"), ("KNEBV.HE", "Kone"), ("SAMPO.HE", "Sampo"),
    ("UPM.HE", "UPM-Kymmene"), ("FORTUM.HE", "Fortum"), ("NESTE.HE", "Neste"),
    ("STERV.HE", "Stora Enso"), ("ORNBV.HE", "Orion"), ("WRT1V.HE", "Wartsila"),
    ("ELISA.HE", "Elisa"), ("METSO.HE", "Metso"), ("KESKOB.HE", "Kesko"),
]
OSLO = [
    ("EQNR.OL", "Equinor"), ("DNB.OL", "DNB Bank"), ("TEL.OL", "Telenor"),
    ("NHY.OL", "Norsk Hydro"), ("MOWI.OL", "Mowi"), ("ORK.OL", "Orkla"),
    ("YAR.OL", "Yara International"), ("AKSO.OL", "Aker Solutions"),
    ("SALM.OL", "SalMar"), ("STB.OL", "Storebrand"), ("KOG.OL", "Kongsberg Gruppen"),
    ("SUBC.OL", "Subsea 7"), ("FRO.OL", "Frontline"),
]
OMX_CPH = [
    ("NOVO-B.CO", "Novo Nordisk"), ("MAERSK-B.CO", "Maersk"), ("DSV.CO", "DSV"),
    ("ORSTED.CO", "Orsted"), ("CARL-B.CO", "Carlsberg"), ("VWS.CO", "Vestas Wind"),
    ("COLO-B.CO", "Coloplast"), ("GN.CO", "GN Store Nord"), ("DANSKE.CO", "Danske Bank"),
    ("TRYG.CO", "Tryg"), ("GMAB.CO", "Genmab"), ("PNDORA.CO", "Pandora"),
    ("DEMANT.CO", "Demant"), ("AMBU-B.CO", "Ambu"), ("ROCK-B.CO", "Rockwool"),
]
# ── Austria (.VI) / Ireland (.IR) / Greece (.AT) — a few majors ───────────────
OTHER_EU = [
    ("OMV.VI", "OMV", "WIENER_BORSE", "EUR"), ("EBS.VI", "Erste Group", "WIENER_BORSE", "EUR"),
    ("VER.VI", "Verbund", "WIENER_BORSE", "EUR"), ("VOE.VI", "Voestalpine", "WIENER_BORSE", "EUR"),
    ("RYA.IR", "Ryanair", "EURONEXT_DUB", "EUR"), ("KRZ.IR", "Kerry Group", "EURONEXT_DUB", "EUR"),
    ("A5G.IR", "AIB Group", "EURONEXT_DUB", "EUR"), ("BIRG.IR", "Bank of Ireland", "EURONEXT_DUB", "EUR"),
    ("ETE.AT", "National Bank of Greece", "ATHEX", "EUR"), ("ALPHA.AT", "Alpha Bank", "ATHEX", "EUR"), ("EUROB.AT", "Eurobank", "ATHEX", "EUR"),
]

# ── Hong Kong — HKEX (.HK), HKD ───────────────────────────────────────────────
HKEX = [
    ("0700.HK", "Tencent"), ("9988.HK", "Alibaba"), ("0941.HK", "China Mobile"),
    ("0939.HK", "China Construction Bank"), ("1398.HK", "ICBC"), ("3988.HK", "Bank of China"),
    ("2318.HK", "Ping An Insurance"), ("1299.HK", "AIA Group"), ("0005.HK", "HSBC Holdings"),
    ("0388.HK", "Hong Kong Exchanges"), ("0883.HK", "CNOOC"), ("0857.HK", "PetroChina"),
    ("0386.HK", "Sinopec"), ("1810.HK", "Xiaomi"), ("3690.HK", "Meituan"),
    ("9618.HK", "JD.com"), ("9999.HK", "NetEase"), ("2020.HK", "Anta Sports"),
    ("1024.HK", "Kuaishou"), ("0016.HK", "Sun Hung Kai Properties"), ("0001.HK", "CK Hutchison"),
    ("2628.HK", "China Life"), ("0762.HK", "China Unicom"),
    ("1177.HK", "Sino Biopharmaceutical"), ("2269.HK", "WuXi Biologics"),
    ("1211.HK", "BYD"), ("0175.HK", "Geely Auto"), ("2015.HK", "Li Auto"),
    ("9868.HK", "XPeng"), ("9866.HK", "NIO"), ("0688.HK", "China Overseas Land"),
    ("1109.HK", "China Resources Land"), ("0027.HK", "Galaxy Entertainment"),
    ("1928.HK", "Sands China"), ("0669.HK", "Techtronic Industries"),
    ("2382.HK", "Sunny Optical"), ("3968.HK", "China Merchants Bank"),
    ("0291.HK", "China Resources Beer"), ("6862.HK", "Haidilao"), ("2331.HK", "Li Ning"),
    # Deepened: the utilities, property and staples that anchor the Hang Seng
    # were missing, leaving the HK read almost entirely tech and banks.
    ("0002.HK", "CLP Holdings"), ("0003.HK", "Hong Kong & China Gas"),
    ("0006.HK", "Power Assets"), ("0012.HK", "Henderson Land"),
    ("0017.HK", "New World Development"), ("0101.HK", "Hang Lung Properties"),
    ("1113.HK", "CK Asset Holdings"), ("0823.HK", "Link REIT"),
    ("0960.HK", "Longfor Group"), ("0288.HK", "WH Group"),
    ("0322.HK", "Tingyi"), ("0151.HK", "Want Want China"),
    ("1876.HK", "Budweiser APAC"), ("2313.HK", "Shenzhou International"),
    ("1044.HK", "Hengan International"), ("0992.HK", "Lenovo Group"),
    ("0981.HK", "SMIC"), ("1093.HK", "CSPC Pharmaceutical"),
    ("6160.HK", "BeiGene"), ("9888.HK", "Baidu"), ("2333.HK", "Great Wall Motor"),
    ("0868.HK", "Xinyi Glass"), ("0836.HK", "China Resources Power"),
    ("1088.HK", "China Shenhua Energy"), ("0267.HK", "CITIC Limited"),
    ("2388.HK", "BOC Hong Kong"), ("1972.HK", "Swire Properties"),
    ("0019.HK", "Swire Pacific"), ("0066.HK", "MTR Corporation"),
]

# ── China A-shares — Shanghai (.SS) & Shenzhen (.SZ), CNY ──────────────────────
SSE = [
    ("600519.SS", "Kweichow Moutai"), ("601398.SS", "ICBC"), ("601857.SS", "PetroChina"),
    ("600036.SS", "China Merchants Bank"), ("601288.SS", "Agricultural Bank of China"),
    ("600028.SS", "Sinopec"), ("601988.SS", "Bank of China"), ("600276.SS", "Hengrui Pharma"),
    ("600030.SS", "CITIC Securities"), ("601318.SS", "Ping An Insurance"),
    ("600887.SS", "Inner Mongolia Yili"), ("601888.SS", "China Tourism Group"),
    ("600900.SS", "China Yangtze Power"), ("601668.SS", "China State Construction"),
    ("600809.SS", "Shanxi Xinghuacun Fen Wine"), ("603288.SS", "Foshan Haitian"),
    ("600309.SS", "Wanhua Chemical"), ("601012.SS", "LONGi Green Energy"),
    ("600690.SS", "Haier Smart Home"), ("601899.SS", "Zijin Mining"),
    ("600585.SS", "Anhui Conch Cement"), ("601166.SS", "Industrial Bank"),
    ("601088.SS", "China Shenhua Energy"), ("600031.SS", "Sany Heavy Industry"),
    ("601628.SS", "China Life Insurance"), ("600104.SS", "SAIC Motor"),
    ("603259.SS", "WuXi AppTec"), ("688981.SS", "SMIC"), ("688111.SS", "Kingsoft Office"),
]
SZSE = [
    ("000858.SZ", "Wuliangye Yibin"), ("000333.SZ", "Midea Group"), ("300750.SZ", "CATL"),
    ("002594.SZ", "BYD"), ("000651.SZ", "Gree Electric"), ("002415.SZ", "Hikvision"),
    ("000001.SZ", "Ping An Bank"), ("002714.SZ", "Muyuan Foods"), ("300760.SZ", "Mindray"),
    ("000725.SZ", "BOE Technology"), ("002475.SZ", "Luxshare Precision"),
    ("300059.SZ", "East Money"), ("000568.SZ", "Luzhou Laojiao"), ("002304.SZ", "Jiangsu Yanghe"),
    ("000002.SZ", "China Vanke"), ("300015.SZ", "Aier Eye Hospital"),
    ("300124.SZ", "Shenzhen Inovance"), ("002352.SZ", "SF Holding"), ("000063.SZ", "ZTE"),
    ("002230.SZ", "iFlytek"),
]

# ── Japan — Tokyo Stock Exchange (.T), JPY ────────────────────────────────────
TSE = [
    ("7203.T", "Toyota Motor"), ("6758.T", "Sony Group"), ("6861.T", "Keyence"),
    ("8306.T", "Mitsubishi UFJ Financial"), ("9984.T", "SoftBank Group"),
    ("6098.T", "Recruit Holdings"), ("9432.T", "NTT"), ("8035.T", "Tokyo Electron"),
    ("4063.T", "Shin-Etsu Chemical"), ("6501.T", "Hitachi"), ("7974.T", "Nintendo"),
    ("8058.T", "Mitsubishi Corp"), ("8031.T", "Mitsui & Co"), ("6902.T", "Denso"),
    ("4502.T", "Takeda Pharmaceutical"), ("6367.T", "Daikin Industries"), ("9433.T", "KDDI"),
    ("8316.T", "Sumitomo Mitsui Financial"), ("8411.T", "Mizuho Financial"),
    ("7267.T", "Honda Motor"), ("6594.T", "Nidec"), ("6954.T", "Fanuc"),
    ("4568.T", "Daiichi Sankyo"), ("4661.T", "Oriental Land"), ("9983.T", "Fast Retailing"),
    ("6273.T", "SMC"), ("7741.T", "Hoya"), ("6981.T", "Murata Manufacturing"),
    ("8001.T", "Itochu"), ("8002.T", "Marubeni"), ("8053.T", "Sumitomo Corp"),
    ("2914.T", "Japan Tobacco"), ("4519.T", "Chugai Pharmaceutical"), ("3382.T", "Seven & i"),
    ("6503.T", "Mitsubishi Electric"), ("6702.T", "Fujitsu"), ("6752.T", "Panasonic"),
    ("7011.T", "Mitsubishi Heavy"), ("7751.T", "Canon"), ("8766.T", "Tokio Marine"),
    ("9020.T", "JR East"), ("9022.T", "JR Central"), ("4901.T", "Fujifilm"),
    ("5108.T", "Bridgestone"), ("7269.T", "Suzuki Motor"), ("6301.T", "Komatsu"),
    # Deepened towards Nikkei-225 breadth. The banks, trading houses and autos
    # were represented; utilities, shipping, steel, chemicals and the domestic
    # consumer names were not, and those are the sectors a Japan regime read
    # actually turns on.
    ("8750.T", "Dai-ichi Life"), ("8591.T", "ORIX"), ("8801.T", "Mitsui Fudosan"),
    ("8802.T", "Mitsubishi Estate"), ("9021.T", "JR West"), ("9101.T", "Nippon Yusen"),
    ("9104.T", "Mitsui O.S.K. Lines"), ("9107.T", "Kawasaki Kisen"),
    ("5401.T", "Nippon Steel"), ("5020.T", "ENEOS Holdings"), ("1605.T", "INPEX"),
    ("9501.T", "Tokyo Electric Power"), ("9503.T", "Kansai Electric Power"),
    ("3407.T", "Asahi Kasei"), ("4005.T", "Sumitomo Chemical"), ("4183.T", "Mitsui Chemicals"),
    ("4452.T", "Kao"), ("4911.T", "Shiseido"), ("2802.T", "Ajinomoto"),
    ("2502.T", "Asahi Group"), ("2503.T", "Kirin Holdings"), ("8267.T", "Aeon"),
    ("4543.T", "Terumo"), ("7733.T", "Olympus"), ("6857.T", "Advantest"),
    ("6645.T", "Omron"), ("6971.T", "Kyocera"),
    ("7013.T", "IHI"), ("6326.T", "Kubota"), ("7752.T", "Ricoh"),
    ("6178.T", "Japan Post Holdings"), ("7201.T", "Nissan Motor"),
    ("4307.T", "Nomura Research Institute"), ("8604.T", "Nomura Holdings"),
    ("4689.T", "LY Corporation"), ("6723.T", "Renesas Electronics"),
    ("6963.T", "Rohm"), ("4523.T", "Eisai"), ("4578.T", "Otsuka Holdings"),
    ("2801.T", "Kikkoman"), ("3402.T", "Toray Industries"), ("5333.T", "NGK Insulators"),
]

# ── Canada — TSX (.TO), CAD (majors) ──────────────────────────────────────────
TSX = [
    ("RY.TO", "Royal Bank of Canada"), ("TD.TO", "Toronto-Dominion Bank"),
    ("ENB.TO", "Enbridge"), ("CNQ.TO", "Canadian Natural Resources"),
    ("BN.TO", "Brookfield"), ("CP.TO", "Canadian Pacific Kansas City"),
    ("CNR.TO", "Canadian National Railway"), ("BMO.TO", "Bank of Montreal"),
    ("BNS.TO", "Bank of Nova Scotia"), ("TRP.TO", "TC Energy"), ("SU.TO", "Suncor Energy"),
    ("CM.TO", "CIBC"), ("SHOP.TO", "Shopify"), ("MFC.TO", "Manulife Financial"),
    ("ATD.TO", "Alimentation Couche-Tard"), ("TRI.TO", "Thomson Reuters"),
    ("NTR.TO", "Nutrien"), ("WCN.TO", "Waste Connections"), ("FTS.TO", "Fortis"),
    ("BCE.TO", "BCE"),
    # Deepened to roughly TSX-60 coverage: twenty names left the Canadian read
    # dominated by banks and pipelines, with no materials or tech to speak of.
    ("T.TO", "Telus"), ("SLF.TO", "Sun Life Financial"), ("NA.TO", "National Bank of Canada"),
    ("IFC.TO", "Intact Financial"), ("POW.TO", "Power Corporation"),
    ("GWO.TO", "Great-West Lifeco"), ("BAM.TO", "Brookfield Asset Management"),
    ("ABX.TO", "Barrick Gold"), ("AEM.TO", "Agnico Eagle Mines"),
    ("FNV.TO", "Franco-Nevada"), ("WPM.TO", "Wheaton Precious Metals"),
    ("K.TO", "Kinross Gold"), ("FM.TO", "First Quantum Minerals"),
    ("TECK-B.TO", "Teck Resources"), ("LUN.TO", "Lundin Mining"),
    ("CCO.TO", "Cameco"), ("CVE.TO", "Cenovus Energy"), ("IMO.TO", "Imperial Oil"),
    ("PPL.TO", "Pembina Pipeline"), ("KEY.TO", "Keyera"),
    ("CSU.TO", "Constellation Software"), ("OTEX.TO", "Open Text"),
    ("GIB-A.TO", "CGI"), ("QSR.TO", "Restaurant Brands International"),
    ("L.TO", "Loblaw"), ("MRU.TO", "Metro"), ("DOL.TO", "Dollarama"),
    ("WN.TO", "George Weston"), ("CTC-A.TO", "Canadian Tire"),
    ("TFII.TO", "TFI International"), ("STN.TO", "Stantec"), ("WSP.TO", "WSP Global"),
    ("EMA.TO", "Emera"), ("H.TO", "Hydro One"), ("CU.TO", "Canadian Utilities"),
    ("AQN.TO", "Algonquin Power"), ("NPI.TO", "Northland Power"),
    ("X.TO", "TMX Group"), ("CIGI.TO", "Colliers International"),
]

# ── Australia — ASX (.AX), AUD (majors) ───────────────────────────────────────
ASX = [
    ("BHP.AX", "BHP Group"), ("CBA.AX", "Commonwealth Bank"), ("CSL.AX", "CSL"),
    ("NAB.AX", "National Australia Bank"), ("WBC.AX", "Westpac"), ("ANZ.AX", "ANZ Group"),
    ("MQG.AX", "Macquarie Group"), ("WES.AX", "Wesfarmers"), ("FMG.AX", "Fortescue"),
    ("WOW.AX", "Woolworths"), ("TLS.AX", "Telstra"), ("RIO.AX", "Rio Tinto"),
    ("TCL.AX", "Transurban"), ("GMG.AX", "Goodman Group"), ("WDS.AX", "Woodside Energy"),
    ("QAN.AX", "Qantas Airways"), ("COL.AX", "Coles Group"), ("ALL.AX", "Aristocrat Leisure"),
    # Deepened to roughly ASX-50: eighteen names was four banks and two miners,
    # which is not enough to read an Australian regime, let alone a commodity one.
    ("STO.AX", "Santos"), ("REA.AX", "REA Group"), ("QBE.AX", "QBE Insurance"),
    ("SUN.AX", "Suncorp Group"), ("ORG.AX", "Origin Energy"), ("S32.AX", "South32"),
    ("JHX.AX", "James Hardie"), ("BXB.AX", "Brambles"), ("ASX.AX", "ASX Limited"),
    ("RMD.AX", "ResMed"), ("XRO.AX", "Xero"), ("CPU.AX", "Computershare"),
    ("SHL.AX", "Sonic Healthcare"), ("COH.AX", "Cochlear"), ("SGP.AX", "Stockland"),
    ("MGR.AX", "Mirvac Group"), ("DXS.AX", "Dexus"), ("VCX.AX", "Vicinity Centres"),
    ("SCG.AX", "Scentre Group"), ("IAG.AX", "Insurance Australia Group"),
    ("AMC.AX", "Amcor"), ("TWE.AX", "Treasury Wine Estates"), ("EDV.AX", "Endeavour Group"),
    ("JBH.AX", "JB Hi-Fi"), ("WTC.AX", "WiseTech Global"), ("NST.AX", "Northern Star"),
    ("EVN.AX", "Evolution Mining"), ("PLS.AX", "Pilbara Minerals"), ("IGO.AX", "IGO Limited"),
    ("MIN.AX", "Mineral Resources"), ("LYC.AX", "Lynas Rare Earths"),
    ("APA.AX", "APA Group"), ("AGL.AX", "AGL Energy"), ("CAR.AX", "CAR Group"),
    ("SEK.AX", "Seek"), ("MPL.AX", "Medibank Private"), ("NHF.AX", "nib holdings"),
    ("BEN.AX", "Bendigo and Adelaide Bank"), ("BOQ.AX", "Bank of Queensland"),
    ("HVN.AX", "Harvey Norman"), ("DMP.AX", "Domino's Pizza Enterprises"),
    ("TPG.AX", "TPG Telecom"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# The markets below were absent entirely. Between them they carry a large share
# of world equity market capitalisation, and their absence was not neutral: the
# macro, cross-asset-correlation and currency-strength modules were reasoning
# about "global" conditions from a universe that was North America, Europe,
# India and a thin slice of East Asia. A regime read built on that is a regional
# read wearing a global label.
#
# Every symbol here is validated against the price vendor by
# scripts/validate_world_symbols.py before it ships. Names that the vendor
# cannot price are removed rather than left in to abstain quietly — see the
# module docstring's rule about fabricated tickers.
# ═══════════════════════════════════════════════════════════════════════════════

# ── South Korea — KRX (.KS), KRW ──────────────────────────────────────────────
KRX = [
    ("005930.KS", "Samsung Electronics"), ("000660.KS", "SK Hynix"),
    ("373220.KS", "LG Energy Solution"), ("207940.KS", "Samsung Biologics"),
    ("005380.KS", "Hyundai Motor"), ("000270.KS", "Kia"),
    ("068270.KS", "Celltrion"), ("005490.KS", "POSCO Holdings"),
    ("035420.KS", "NAVER"), ("051910.KS", "LG Chem"),
    ("006400.KS", "Samsung SDI"), ("105560.KS", "KB Financial Group"),
    ("055550.KS", "Shinhan Financial Group"), ("012330.KS", "Hyundai Mobis"),
    ("034730.KS", "SK Inc"), ("015760.KS", "Korea Electric Power"),
    ("032830.KS", "Samsung Life Insurance"), ("086790.KS", "Hana Financial Group"),
    ("316140.KS", "Woori Financial Group"), ("009150.KS", "Samsung Electro-Mechanics"),
    ("010130.KS", "Korea Zinc"), ("035720.KS", "Kakao"),
    ("018260.KS", "Samsung SDS"), ("090430.KS", "Amorepacific"),
    ("097950.KS", "CJ CheilJedang"), ("010950.KS", "S-Oil"),
    ("011200.KS", "HMM"), ("024110.KS", "Industrial Bank of Korea"),
    ("011170.KS", "Lotte Chemical"), ("021240.KS", "Coway"),
    ("161390.KS", "Hankook Tire & Technology"), ("259960.KS", "Krafton"),
    ("302440.KS", "SK Bioscience"), ("128940.KS", "Hanmi Pharm"),
    ("003670.KS", "POSCO Future M"),
]

# ── Taiwan — TWSE (.TW), TWD ──────────────────────────────────────────────────
TWSE = [
    ("2330.TW", "TSMC"), ("2317.TW", "Hon Hai Precision"),
    ("2454.TW", "MediaTek"), ("2308.TW", "Delta Electronics"),
    ("2382.TW", "Quanta Computer"), ("2412.TW", "Chunghwa Telecom"),
    ("2881.TW", "Fubon Financial"), ("2882.TW", "Cathay Financial"),
    ("1301.TW", "Formosa Plastics"), ("1303.TW", "Nan Ya Plastics"),
    ("2303.TW", "United Microelectronics"), ("3711.TW", "ASE Technology"),
    ("2891.TW", "CTBC Financial"), ("2886.TW", "Mega Financial"),
    ("2002.TW", "China Steel"), ("2207.TW", "Hotai Motor"),
    ("3008.TW", "Largan Precision"), ("2357.TW", "Asustek Computer"),
    ("2409.TW", "AU Optronics"), ("6505.TW", "Formosa Petrochemical"),
    ("2884.TW", "E.Sun Financial"), ("2885.TW", "Yuanta Financial"),
    ("1216.TW", "Uni-President Enterprises"), ("2892.TW", "First Financial"),
    ("2880.TW", "Hua Nan Financial"), ("2379.TW", "Realtek Semiconductor"),
    ("3034.TW", "Novatek Microelectronics"), ("2327.TW", "Yageo"),
    ("2603.TW", "Evergreen Marine"), ("2609.TW", "Yang Ming Marine"),
]

# ── Brazil — B3 (.SA), BRL ────────────────────────────────────────────────────
B3 = [
    ("PETR4.SA", "Petrobras PN"), ("PETR3.SA", "Petrobras ON"),
    ("VALE3.SA", "Vale"), ("ITUB4.SA", "Itau Unibanco"),
    ("BBDC4.SA", "Bradesco PN"), ("ABEV3.SA", "Ambev"),
    ("B3SA3.SA", "B3"), ("WEGE3.SA", "WEG"),
    ("BBAS3.SA", "Banco do Brasil"), ("ITSA4.SA", "Itausa"),
    ("RENT3.SA", "Localiza"), ("SUZB3.SA", "Suzano"),
    ("PRIO3.SA", "PRIO"), ("EQTL3.SA", "Equatorial Energia"),
    ("RAIL3.SA", "Rumo"), ("GGBR4.SA", "Gerdau"),
    ("CSNA3.SA", "CSN"), ("LREN3.SA", "Lojas Renner"), ("RADL3.SA", "Raia Drogasil"),
    ("HAPV3.SA", "Hapvida"), ("VIVT3.SA", "Telefonica Brasil"),
    ("CMIG4.SA", "Cemig"), ("UGPA3.SA", "Ultrapar"),
    ("KLBN11.SA", "Klabin"), ("TOTS3.SA", "TOTVS"),
    ("CPLE3.SA", "Copel"), ("SBSP3.SA", "Sabesp"),
    ("TIMS3.SA", "TIM Brasil"), ("MULT3.SA", "Multiplan"),
    ("MOTV3.SA", "Motiva (ex-CCR)"), ("NATU3.SA", "Natura &Co"),
    ]

# ── Mexico — BMV (.MX), MXN ───────────────────────────────────────────────────
BMV = [
    ("WALMEX.MX", "Walmart de Mexico"), ("FEMSAUBD.MX", "FEMSA"),
    ("GFNORTEO.MX", "Grupo Financiero Banorte"), ("GMEXICOB.MX", "Grupo Mexico"),
    ("CEMEXCPO.MX", "Cemex"), ("TLEVISACPO.MX", "Grupo Televisa"),
    ("KOFUBL.MX", "Coca-Cola FEMSA"), ("ASURB.MX", "Grupo Aeroportuario del Sureste"),
    ("GAPB.MX", "Grupo Aeroportuario del Pacifico"), ("OMAB.MX", "Grupo Aeroportuario Centro Norte"),
    ("ALSEA.MX", "Alsea"), ("BIMBOA.MX", "Grupo Bimbo"),
    ("ORBIA.MX", "Orbia"),
    ("KIMBERA.MX", "Kimberly-Clark de Mexico"), ("AC.MX", "Arca Continental"),
    ("PINFRA.MX", "Pinfra"), ("VESTA.MX", "Corporacion Inmobiliaria Vesta"),
    ("CUERVO.MX", "Becle"), ("AMXB.MX", "America Movil"),
    ("GCARSOA1.MX", "Grupo Carso"), ("Q.MX", "Quimica del Rey"),
]

# ── South Africa — JSE (.JO), ZAR ─────────────────────────────────────────────
JSE = [
    ("NPN.JO", "Naspers"), ("PRX.JO", "Prosus"), ("FSR.JO", "FirstRand"),
    ("SBK.JO", "Standard Bank"), ("ABG.JO", "Absa Group"), ("NED.JO", "Nedbank"),
    ("MTN.JO", "MTN Group"), ("VOD.JO", "Vodacom"), ("SOL.JO", "Sasol"),
    ("AGL.JO", "Anglo American"), ("BHG.JO", "BHP Group"), ("IMP.JO", "Impala Platinum"),
    ("GFI.JO", "Gold Fields"),
    ("ANG.JO", "AngloGold Ashanti"), ("SHP.JO", "Shoprite"), ("CPI.JO", "Capitec Bank"),
    ("BID.JO", "Bid Corp"), ("BVT.JO", "Bidvest"), ("CLS.JO", "Clicks Group"),
    ("WHL.JO", "Woolworths Holdings"), ("TFG.JO", "Foschini Group"),
    ("DSY.JO", "Discovery"), ("SLM.JO", "Sanlam"), ("OMU.JO", "Old Mutual"),
    ("REM.JO", "Remgro"), ("EXX.JO", "Exxaro Resources"), ("KIO.JO", "Kumba Iron Ore"),
    ("HAR.JO", "Harmony Gold"), ("ARI.JO", "African Rainbow Minerals"),
]

# ── Singapore — SGX (.SI), SGD ────────────────────────────────────────────────
SGX = [
    ("D05.SI", "DBS Group"), ("O39.SI", "OCBC Bank"), ("U11.SI", "United Overseas Bank"),
    ("Z74.SI", "Singtel"), ("C6L.SI", "Singapore Airlines"),
    ("C38U.SI", "CapitaLand Integrated Commercial Trust"), ("A17U.SI", "CapitaLand Ascendas REIT"),
    ("BN4.SI", "Keppel"), ("S63.SI", "ST Engineering"), ("F34.SI", "Wilmar International"),
    ("Y92.SI", "Thai Beverage"), ("U96.SI", "Sembcorp Industries"),
    ("G13.SI", "Genting Singapore"), ("C09.SI", "City Developments"),
    ("H78.SI", "Hongkong Land"), ("J36.SI", "Jardine Matheson"),
    ("ME8U.SI", "Mapletree Industrial Trust"), ("S68.SI", "Singapore Exchange"),
    ("V03.SI", "Venture Corporation"), ("U14.SI", "UOL Group"),
    ("C07.SI", "Jardine Cycle & Carriage"), ("BS6.SI", "Yangzijiang Shipbuilding"),
    ("D01.SI", "DFI Retail Group"), ("AJBU.SI", "Keppel DC REIT"),
]

# ── Saudi Arabia — Tadawul (.SR), SAR ─────────────────────────────────────────
TADAWUL = [
    ("2222.SR", "Saudi Aramco"), ("1120.SR", "Al Rajhi Bank"),
    ("2010.SR", "SABIC"), ("7010.SR", "Saudi Telecom"),
    ("1211.SR", "Maaden"), ("1180.SR", "Saudi National Bank"),
    ("2350.SR", "Saudi Kayan"), ("1010.SR", "Riyad Bank"),
    ("1150.SR", "Alinma Bank"), ("2280.SR", "Almarai"),
    ("1050.SR", "Banque Saudi Fransi"), ("1060.SR", "Saudi Awwal Bank"),
    ("4001.SR", "Abdullah Al Othaim Markets"), ("4190.SR", "Jarir Marketing"),
    ("4002.SR", "Mouwasat Medical Services"), ("8010.SR", "Tawuniya"),
    ("4013.SR", "Dr Sulaiman Al Habib Medical"), ("2380.SR", "Petro Rabigh"),
    ("3030.SR", "Saudi Cement"), ("1810.SR", "Seera Group"),
]

# ── Indonesia — IDX (.JK), IDR ────────────────────────────────────────────────
IDX = [
    ("BBCA.JK", "Bank Central Asia"), ("BBRI.JK", "Bank Rakyat Indonesia"),
    ("BMRI.JK", "Bank Mandiri"), ("BBNI.JK", "Bank Negara Indonesia"),
    ("TLKM.JK", "Telkom Indonesia"), ("ASII.JK", "Astra International"),
    ("UNVR.JK", "Unilever Indonesia"), ("ICBP.JK", "Indofood CBP"),
    ("INDF.JK", "Indofood Sukses Makmur"), ("KLBF.JK", "Kalbe Farma"),
    ("GGRM.JK", "Gudang Garam"), ("HMSP.JK", "HM Sampoerna"),
    ("ADRO.JK", "Adaro Energy"), ("PTBA.JK", "Bukit Asam"),
    ("ITMG.JK", "Indo Tambangraya Megah"), ("ANTM.JK", "Aneka Tambang"),
    ("INCO.JK", "Vale Indonesia"), ("SMGR.JK", "Semen Indonesia"),
    ("INTP.JK", "Indocement"), ("CPIN.JK", "Charoen Pokphand Indonesia"),
    ("UNTR.JK", "United Tractors"), ("PGAS.JK", "Perusahaan Gas Negara"),
    ("TOWR.JK", "Sarana Menara Nusantara"), ("EXCL.JK", "XL Axiata"),
    ("MDKA.JK", "Merdeka Copper Gold"), ("AMRT.JK", "Sumber Alfaria Trijaya"),
    ("BRPT.JK", "Barito Pacific"), ("TPIA.JK", "Chandra Asri"),
]

# ── Thailand — SET (.BK), THB ─────────────────────────────────────────────────
SET = [
    ("PTT.BK", "PTT"), ("PTTEP.BK", "PTT Exploration & Production"),
    ("AOT.BK", "Airports of Thailand"), ("CPALL.BK", "CP All"),
    ("SCB.BK", "SCB X"), ("KBANK.BK", "Kasikornbank"),
    ("BBL.BK", "Bangkok Bank"), ("KTB.BK", "Krung Thai Bank"),
    ("ADVANC.BK", "Advanced Info Service"), ("SCC.BK", "Siam Cement"), ("CPN.BK", "Central Pattana"),
    ("BDMS.BK", "Bangkok Dusit Medical"), ("BH.BK", "Bumrungrad Hospital"),
    ("GULF.BK", "Gulf Energy Development"), ("EA.BK", "Energy Absolute"),
    ("TRUE.BK", "True Corporation"), ("MINT.BK", "Minor International"),
    ("CRC.BK", "Central Retail"), ("HMPRO.BK", "Home Product Center"),
    ("GPSC.BK", "Global Power Synergy"), ("TOP.BK", "Thai Oil"),
    ("IVL.BK", "Indorama Ventures"), ("BANPU.BK", "Banpu"),
    ("OSP.BK", "Osotspa"), ("TU.BK", "Thai Union Group"),
    ("KTC.BK", "Krungthai Card"), ("LH.BK", "Land & Houses"),
    ("TISCO.BK", "Tisco Financial"), ("EGCO.BK", "Electricity Generating"),
]

# ── Malaysia — Bursa (.KL), MYR ───────────────────────────────────────────────
BURSA = [
    ("1155.KL", "Malayan Banking"), ("1023.KL", "CIMB Group"),
    ("5347.KL", "Tenaga Nasional"), ("1295.KL", "Public Bank"),
    ("6888.KL", "Axiata Group"), ("4863.KL", "Telekom Malaysia"),
    ("1961.KL", "IOI Corporation"), ("2445.KL", "Kuala Lumpur Kepong"),
    ("4197.KL", "Sime Darby"), ("6033.KL", "Petronas Gas"),
    ("5681.KL", "Petronas Dagangan"), ("5183.KL", "Petronas Chemicals"),
    ("3182.KL", "Genting"), ("4715.KL", "Genting Malaysia"),
    ("1082.KL", "Hong Leong Financial Group"), ("5819.KL", "Hong Leong Bank"),
    ("6947.KL", "CelcomDigi"), ("7277.KL", "Dialog Group"),
    ("5225.KL", "IHH Healthcare"), ("4707.KL", "Nestle Malaysia"),
    ("3816.KL", "MISC"), ("1015.KL", "AMMB Holdings"),
    ("2291.KL", "Genting Plantations"),
]

# ── New Zealand — NZX (.NZ), NZD ──────────────────────────────────────────────
NZX = [
    ("ATM.NZ", "a2 Milk"), ("FPH.NZ", "Fisher & Paykel Healthcare"),
    ("AIA.NZ", "Auckland International Airport"), ("MEL.NZ", "Meridian Energy"),
    ("SPK.NZ", "Spark New Zealand"), ("CEN.NZ", "Contact Energy"),
    ("MCY.NZ", "Mercury NZ"), ("EBO.NZ", "EBOS Group"),
    ("RYM.NZ", "Ryman Healthcare"), ("IFT.NZ", "Infratil"),
    ("FBU.NZ", "Fletcher Building"), ("GNE.NZ", "Genesis Energy"),
    ("POT.NZ", "Port of Tauranga"), ("SUM.NZ", "Summerset Group"),
    ("VCT.NZ", "Vector"), ("KPG.NZ", "Kiwi Property Group"),
    ("SKC.NZ", "SkyCity Entertainment"), ("PCT.NZ", "Precinct Properties"),
]

# ── Turkey — Borsa Istanbul (.IS), TRY ────────────────────────────────────────
BIST = [
    ("THYAO.IS", "Turkish Airlines"), ("GARAN.IS", "Garanti BBVA"),
    ("AKBNK.IS", "Akbank"), ("ISCTR.IS", "Isbank"),
    ("BIMAS.IS", "BIM Birlesik Magazalar"), ("TUPRS.IS", "Tupras"),
    ("EREGL.IS", "Eregli Demir Celik"), ("KCHOL.IS", "Koc Holding"),
    ("SAHOL.IS", "Sabanci Holding"), ("ASELS.IS", "Aselsan"),
    ("SISE.IS", "Sisecam"), ("FROTO.IS", "Ford Otosan"),
    ("TOASO.IS", "Tofas"), ("PGSUS.IS", "Pegasus Airlines"),
    ("YKBNK.IS", "Yapi Kredi"), ("VAKBN.IS", "Vakifbank"),
    ("HALKB.IS", "Halkbank"), ("TCELL.IS", "Turkcell"),
    ("TTKOM.IS", "Turk Telekom"), ("ARCLK.IS", "Arcelik"),
    ("PETKM.IS", "Petkim"),
    ("ENKAI.IS", "Enka Insaat"), ("MGROS.IS", "Migros"),
    ("ULKER.IS", "Ulker Biskuvi"), ("SASA.IS", "Sasa Polyester"),
    ("ALARK.IS", "Alarko Holding"), ("DOHOL.IS", "Dogan Holding"),
]

# ── Poland — GPW (.WA), PLN ───────────────────────────────────────────────────
GPW = [
    ("PKO.WA", "PKO Bank Polski"), ("PKN.WA", "Orlen"),
    ("PZU.WA", "PZU"), ("PEO.WA", "Bank Pekao"),
    ("DNP.WA", "Dino Polska"),
    ("LPP.WA", "LPP"), ("KGH.WA", "KGHM Polska Miedz"),
    ("ALE.WA", "Allegro"), ("CDR.WA", "CD Projekt"),
    ("CPS.WA", "Cyfrowy Polsat"), ("MBK.WA", "mBank"),
    ("OPL.WA", "Orange Polska"), ("JSW.WA", "Jastrzebska Spolka Weglowa"),
    ("TPE.WA", "Tauron"), ("PGE.WA", "PGE"),
    ("ACP.WA", "Asseco Poland"), ("KTY.WA", "Grupa Kety"),
    ("BDX.WA", "Budimex"),
]

# currency + suffix per exchange label
CURRENCY = {
    "XETRA": "EUR", "EURONEXT_PA": "EUR", "EURONEXT_AS": "EUR", "BORSA_IT": "EUR",
    "BME": "EUR", "SIX": "CHF", "OMX_STO": "SEK", "EURONEXT_BR": "EUR",
    "EURONEXT_LS": "EUR", "OMX_HEL": "EUR", "OSLO": "NOK", "OMX_CPH": "DKK",
    "WIENER_BORSE": "EUR", "EURONEXT_DUB": "EUR", "ATHEX": "EUR",
    "HKEX": "HKD", "SSE": "CNY", "SZSE": "CNY", "TSE": "JPY",
    "TSX": "CAD", "ASX": "AUD",
    "KRX": "KRW", "TWSE": "TWD", "B3": "BRL", "BMV": "MXN", "JSE": "ZAR",
    "SGX": "SGD", "TADAWUL": "SAR", "IDX": "IDR", "SET": "THB", "BURSA": "MYR",
    "NZX": "NZD", "BIST": "TRY", "GPW": "PLN",
}


def all_world_symbols():
    """Yield (display=yahoo, yahoo, name, exchange, currency, asset_class)."""
    simple = {
        "XETRA": XETRA, "EURONEXT_PA": EURONEXT_PA, "EURONEXT_AS": EURONEXT_AS,
        "BORSA_IT": BORSA_IT, "BME": BME, "SIX": SIX, "OMX_STO": OMX_STO,
        "EURONEXT_BR": EURONEXT_BR, "EURONEXT_LS": EURONEXT_LS, "OMX_HEL": OMX_HEL,
        "OSLO": OSLO, "OMX_CPH": OMX_CPH, "HKEX": HKEX, "SSE": SSE, "SZSE": SZSE,
        "TSE": TSE, "TSX": TSX, "ASX": ASX,
        "KRX": KRX, "TWSE": TWSE, "B3": B3, "BMV": BMV, "JSE": JSE,
        "SGX": SGX, "TADAWUL": TADAWUL, "IDX": IDX, "SET": SET, "BURSA": BURSA,
        "NZX": NZX, "BIST": BIST, "GPW": GPW,
    }
    for exch, rows in simple.items():
        cur = CURRENCY.get(exch, "")
        for yahoo, name in rows:
            yield (yahoo, yahoo, name, exch, cur, "equity")
    # OTHER_EU carries its own exchange + currency per row
    for yahoo, name, exch, cur in OTHER_EU:
        yield (yahoo, yahoo, name, exch, cur, "equity")
