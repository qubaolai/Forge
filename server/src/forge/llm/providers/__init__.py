"""LLM Providers 包.

各模块在 import 时通过 @register_llm 装饰器自动注册到工厂.
不需要在这里显式 re-export, 由 gateway._autoload() 负责触发.
"""
