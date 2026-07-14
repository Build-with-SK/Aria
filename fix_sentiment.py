content = open("main.py", encoding="utf-8").read()
start = content.find("def run_sentiment")
print(content[start:start+500])
