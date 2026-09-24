"""
Built-in NSE large-cap universe used to seed an empty watchlist.

Each entry: (NSE symbol, display name, sector, news keywords).

- The display name is what the left panel shows (the PRD asks for stock names
  only), so it's the short name traders actually use, not the legal name.
- Keywords are what market_service matches against news headlines. They are
  curated because naive matching on the first word of the company name makes
  every "Tata ..." headline count for every Tata company. Keywords of four or
  fewer characters that are all caps (e.g. "BEL", "HAL") are matched
  case-sensitively so they don't hit ordinary words.

All symbols were verified against Yahoo Finance (SYMBOL.NS) on 2026-09-24.
Tata Motors trades as TMPV since the 2025 demerger.
"""

UNIVERSE: list[tuple[str, str, str, list[str]]] = [
    ("RELIANCE", "Reliance", "Energy", ["Reliance Industries", "RIL", "Reliance Jio", "Reliance Retail"]),
    ("TCS", "TCS", "IT", ["TCS", "Tata Consultancy"]),
    ("HDFCBANK", "HDFC Bank", "Banking", ["HDFC Bank"]),
    ("ICICIBANK", "ICICI Bank", "Banking", ["ICICI Bank"]),
    ("INFY", "Infosys", "IT", ["Infosys"]),
    ("BHARTIARTL", "Bharti Airtel", "Telecom", ["Airtel", "Bharti"]),
    ("SBIN", "SBI", "Banking", ["SBI", "State Bank of India"]),
    ("ITC", "ITC", "FMCG", ["ITC"]),
    ("LT", "L&T", "Capital Goods", ["L&T", "Larsen"]),
    ("HINDUNILVR", "HUL", "FMCG", ["HUL", "Hindustan Unilever"]),
    ("KOTAKBANK", "Kotak Bank", "Banking", ["Kotak Mahindra Bank", "Kotak Bank"]),
    ("AXISBANK", "Axis Bank", "Banking", ["Axis Bank"]),
    ("BAJFINANCE", "Bajaj Finance", "Financials", ["Bajaj Finance"]),
    ("MARUTI", "Maruti Suzuki", "Auto", ["Maruti"]),
    ("SUNPHARMA", "Sun Pharma", "Pharma", ["Sun Pharma"]),
    ("HCLTECH", "HCL Tech", "IT", ["HCL Tech", "HCLTech", "HCL Technologies"]),
    ("M&M", "Mahindra & Mahindra", "Auto", ["Mahindra & Mahindra", "M&M"]),
    ("TITAN", "Titan", "Consumer", ["Titan"]),
    ("ULTRACEMCO", "UltraTech Cement", "Cement", ["UltraTech"]),
    ("NTPC", "NTPC", "Power", ["NTPC"]),
    ("POWERGRID", "Power Grid", "Power", ["Power Grid"]),
    ("ONGC", "ONGC", "Energy", ["ONGC"]),
    ("TATASTEEL", "Tata Steel", "Metals", ["Tata Steel"]),
    ("ADANIENT", "Adani Enterprises", "Conglomerate", ["Adani Enterprises"]),
    ("ADANIPORTS", "Adani Ports", "Infrastructure", ["Adani Ports"]),
    ("ASIANPAINT", "Asian Paints", "Consumer", ["Asian Paints"]),
    ("BAJAJFINSV", "Bajaj Finserv", "Financials", ["Bajaj Finserv"]),
    ("COALINDIA", "Coal India", "Mining", ["Coal India"]),
    ("JSWSTEEL", "JSW Steel", "Metals", ["JSW Steel"]),
    ("NESTLEIND", "Nestle India", "FMCG", ["Nestle India", "Nestlé India"]),
    ("WIPRO", "Wipro", "IT", ["Wipro"]),
    ("TECHM", "Tech Mahindra", "IT", ["Tech Mahindra"]),
    ("GRASIM", "Grasim", "Cement", ["Grasim"]),
    ("HINDALCO", "Hindalco", "Metals", ["Hindalco"]),
    ("DRREDDY", "Dr Reddy's", "Pharma", ["Dr Reddy", "Dr. Reddy"]),
    ("CIPLA", "Cipla", "Pharma", ["Cipla"]),
    ("BAJAJ-AUTO", "Bajaj Auto", "Auto", ["Bajaj Auto"]),
    ("EICHERMOT", "Eicher Motors", "Auto", ["Eicher", "Royal Enfield"]),
    ("HEROMOTOCO", "Hero MotoCorp", "Auto", ["Hero MotoCorp"]),
    ("BRITANNIA", "Britannia", "FMCG", ["Britannia"]),
    ("APOLLOHOSP", "Apollo Hospitals", "Healthcare", ["Apollo Hospitals"]),
    ("DIVISLAB", "Divi's Labs", "Pharma", ["Divi's", "Divis Lab"]),
    ("SBILIFE", "SBI Life", "Insurance", ["SBI Life"]),
    ("HDFCLIFE", "HDFC Life", "Insurance", ["HDFC Life"]),
    ("INDUSINDBK", "IndusInd Bank", "Banking", ["IndusInd"]),
    ("TRENT", "Trent", "Retail", ["Trent", "Zudio"]),
    ("BEL", "BEL", "Defence", ["BEL", "Bharat Electronics"]),
    ("HAL", "HAL", "Defence", ["HAL", "Hindustan Aeronautics"]),
    ("SHRIRAMFIN", "Shriram Finance", "Financials", ["Shriram Finance"]),
    ("TMPV", "Tata Motors", "Auto", ["Tata Motors", "JLR", "Jaguar Land Rover"]),
    ("TATACONSUM", "Tata Consumer", "FMCG", ["Tata Consumer"]),
    ("JIOFIN", "Jio Financial", "Financials", ["Jio Financial"]),
    ("ETERNAL", "Eternal (Zomato)", "Consumer Tech", ["Eternal", "Zomato", "Blinkit"]),
    ("MAXHEALTH", "Max Healthcare", "Healthcare", ["Max Healthcare"]),
    ("INDIGO", "IndiGo", "Aviation", ["IndiGo", "InterGlobe"]),
]
