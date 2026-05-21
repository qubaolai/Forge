# """检索调试接口。

# 不走 LLM,直接返回 retriever 的检索结果,方便前端调参/排查。
# 对应 FRONTEND_BACKEND_SPEC.md 3.5 节。
# """
# from __future__ import annotations

# from dataclasses import asdict

# from fastapi import APIRouter, Depends

# from forge.api.dependencies import get_retriever
# from forge.api.schemas.retrieval import SearchRequest, SearchResponse

# router = APIRouter()


# @router.post("/search", response_model=SearchResponse)
# async def search(
#     body: SearchRequest,
#     retriever=Depends(get_retriever),
# ) -> SearchResponse:
#     results = retriever.retrieve(
#         query=body.query,
#         doc_id_filter=body.doc_id_filter,
#         top_n=body.top_n,
#     )
#     # RetrievedParent 是 dataclass,asdict 转字典后由 Pydantic 校验
#     return SearchResponse(
#         query=body.query,
#         items=[asdict(r) for r in results],
#     )
