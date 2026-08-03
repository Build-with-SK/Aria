"""
world_symbols.py
================
Curated REAL large-cap constituents for markets that don't publish a free,
urllib-friendly full listing (Europe, China, Hong Kong, Japan). These are the
major index members (DAX/CAC/AEX/IBEX/FTSE-MIB/SMI/OMX/etc., HSI, CSI, Nikkei),
used to give the universe real geographic breadth alongside the live full
listings (US via NASDAQ Trader, NSE + BSE via official bhavcopies).

Each entry: (yahoo_symbol, name, exchange, currency). The display symbol IS the
yahoo symbol (suffix included) so cross-exchange collisions are impossible
(e.g. SAN.PA Sanofi vs SAN.MC Santander stay distinct). asset_class = equity.

Not exhaustive — real, verifiable names only; no fabricated tickers.
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
    ("MC.PA", "LVMH"), ("OR.PA", "L'Oreal"), ("TTE.PA", "TotalEnergies"),
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
    ("ASML.AS", "ASML"), ("ADYEN.AS", "Adyen"), ("PRX.AS", "Prosus"),
    ("INGA.AS", "ING Group"), ("HEIA.AS", "Heineken"), ("PHIA.AS", "Philips"),
    ("WKL.AS", "Wolters Kluwer"), ("AD.AS", "Ahold Delhaize"), ("ABN.AS", "ABN AMRO"),
    ("AKZA.AS", "Akzo Nobel"), ("KPN.AS", "KPN"), ("RAND.AS", "Randstad"),
    ("ASM.AS", "ASM International"), ("BESI.AS", "BE Semiconductor"),
    ("IMCD.AS", "IMCD"), ("MT.AS", "ArcelorMittal"), ("NN.AS", "NN Group"),
    ("AGN.AS", "Aegon"), ("URW.AS", "Unibail-Rodamco-Westfield"), ("DSFIR.AS", "DSM-Firmenich"),
]

# ── Italy — Borsa Italiana (.MI), EUR ─────────────────────────────────────────
BORSA_IT = [
    ("ENI.MI", "Eni"), ("ISP.MI", "Intesa Sanpaolo"), ("UCG.MI", "UniCredit"),
    ("ENEL.MI", "Enel"), ("STLAM.MI", "Stellantis"), ("G.MI", "Generali"),
    ("STM.MI", "STMicroelectronics"), ("RACE.MI", "Ferrari"), ("TIT.MI", "Telecom Italia"),
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
    ("NESN.SW", "Nestle"), ("ROG.SW", "Roche"), ("NOVN.SW", "Novartis"),
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
    ("MELE.BR", "Melexis"), ("ELI.BR", "Elia"), ("APAM.BR", "Aperam"),
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
    ("RYAAY.IR", "Ryanair", "EURONEXT_DUB", "EUR"), ("KRZ.IR", "Kerry Group", "EURONEXT_DUB", "EUR"),
    ("A5G.IR", "AIB Group", "EURONEXT_DUB", "EUR"), ("BIRG.IR", "Bank of Ireland", "EURONEXT_DUB", "EUR"),
    ("ETE.AT", "National Bank of Greece", "ATHEX", "EUR"), ("OPAP.AT", "OPAP", "ATHEX", "EUR"),
    ("ALPHA.AT", "Alpha Bank", "ATHEX", "EUR"), ("EUROB.AT", "Eurobank", "ATHEX", "EUR"),
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
    ("0011.HK", "Hang Seng Bank"), ("2628.HK", "China Life"), ("0762.HK", "China Unicom"),
    ("1177.HK", "Sino Biopharmaceutical"), ("2269.HK", "WuXi Biologics"),
    ("1211.HK", "BYD"), ("0175.HK", "Geely Auto"), ("2015.HK", "Li Auto"),
    ("9868.HK", "XPeng"), ("9866.HK", "NIO"), ("0688.HK", "China Overseas Land"),
    ("1109.HK", "China Resources Land"), ("0027.HK", "Galaxy Entertainment"),
    ("1928.HK", "Sands China"), ("0669.HK", "Techtronic Industries"),
    ("2382.HK", "Sunny Optical"), ("3968.HK", "China Merchants Bank"),
    ("0291.HK", "China Resources Beer"), ("6862.HK", "Haidilao"), ("2331.HK", "Li Ning"),
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
]

# ── Australia — ASX (.AX), AUD (majors) ───────────────────────────────────────
ASX = [
    ("BHP.AX", "BHP Group"), ("CBA.AX", "Commonwealth Bank"), ("CSL.AX", "CSL"),
    ("NAB.AX", "National Australia Bank"), ("WBC.AX", "Westpac"), ("ANZ.AX", "ANZ Group"),
    ("MQG.AX", "Macquarie Group"), ("WES.AX", "Wesfarmers"), ("FMG.AX", "Fortescue"),
    ("WOW.AX", "Woolworths"), ("TLS.AX", "Telstra"), ("RIO.AX", "Rio Tinto"),
    ("TCL.AX", "Transurban"), ("GMG.AX", "Goodman Group"), ("WDS.AX", "Woodside Energy"),
    ("QAN.AX", "Qantas Airways"), ("COL.AX", "Coles Group"), ("ALL.AX", "Aristocrat Leisure"),
]

# currency + suffix per exchange label
CURRENCY = {
    "XETRA": "EUR", "EURONEXT_PA": "EUR", "EURONEXT_AS": "EUR", "BORSA_IT": "EUR",
    "BME": "EUR", "SIX": "CHF", "OMX_STO": "SEK", "EURONEXT_BR": "EUR",
    "EURONEXT_LS": "EUR", "OMX_HEL": "EUR", "OSLO": "NOK", "OMX_CPH": "DKK",
    "WIENER_BORSE": "EUR", "EURONEXT_DUB": "EUR", "ATHEX": "EUR",
    "HKEX": "HKD", "SSE": "CNY", "SZSE": "CNY", "TSE": "JPY",
    "TSX": "CAD", "ASX": "AUD",
}


def all_world_symbols():
    """Yield (display=yahoo, yahoo, name, exchange, currency, asset_class)."""
    simple = {
        "XETRA": XETRA, "EURONEXT_PA": EURONEXT_PA, "EURONEXT_AS": EURONEXT_AS,
        "BORSA_IT": BORSA_IT, "BME": BME, "SIX": SIX, "OMX_STO": OMX_STO,
        "EURONEXT_BR": EURONEXT_BR, "EURONEXT_LS": EURONEXT_LS, "OMX_HEL": OMX_HEL,
        "OSLO": OSLO, "OMX_CPH": OMX_CPH, "HKEX": HKEX, "SSE": SSE, "SZSE": SZSE,
        "TSE": TSE, "TSX": TSX, "ASX": ASX,
    }
    for exch, rows in simple.items():
        cur = CURRENCY.get(exch, "")
        for yahoo, name in rows:
            yield (yahoo, yahoo, name, exch, cur, "equity")
    # OTHER_EU carries its own exchange + currency per row
    for yahoo, name, exch, cur in OTHER_EU:
        yield (yahoo, yahoo, name, exch, cur, "equity")
