import requests

url = "https://openrouter.ai/api/v1/models"
response = requests.get(url)

if response.status_code == 200:
    models_data = response.json().get('data', [])
    # 打印前3个模型的详细信息作为示例
    for model in models_data[:3]:
        print(f"模型ID: {model['id']}")
        print(f"名称: {model['name']}")
        print(f"上下文窗口: {model['context_length']} tokens")
        print(f"价格 (每百万输入Token): ${float(model['pricing']['prompt']) * 1000000:.2f}")
        print(f"简介: {model['description']}\n" + "-"*40)