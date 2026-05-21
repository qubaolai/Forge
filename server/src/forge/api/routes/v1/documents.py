# """文档管理接口。

# - POST /upload    上传文档(转 IngestService)
# - GET  /         列表(查 documents 表)
# - GET  /{doc_id} 单条详情
# - DELETE /{doc_id} 删除文档(级联清理向量库 / BM25 / MySQL)

# 注意:
# - /upload 当前同步入库(简单);后续如需异步,改成放队列 + worker
# - chunking 调用待 ingest 路由真实实现时补上
# """
# from __future__ import annotations

# from fastapi import APIRouter, Depends, UploadFile

# from forge.api.dependencies import get_document_service, get_ingest_service
# from forge.api.schemas.document import DocumentInfo, DocumentListResponse, UploadResponse

# router = APIRouter()


# @router.post("/upload", response_model=UploadResponse)
# async def upload_document(
#     file: UploadFile,
#     ingest_service=Depends(get_ingest_service),
# ) -> UploadResponse:
#     """TODO:
#     1. 把 UploadFile 暂存到 settings.ingest.documents_path
#     2. 调 parser_dispatcher 解析
#     3. 用 chunking_selector 选切分策略
#     4. 调用 ingest_service.ingest(file_path, chunks_provider)
#     """
#     return UploadResponse(
#         doc_id="placeholder",
#         filename=file.filename or "",
#         status="queued",
#     )


# @router.get("", response_model=DocumentListResponse)
# async def list_documents(
#     page: int = 1,
#     page_size: int = 20,
#     service=Depends(get_document_service),
# ) -> DocumentListResponse:
#     items, total = service.list_documents(page=page, page_size=page_size)
#     return DocumentListResponse(
#         items=items,
#         total=total,
#         page=page,
#         page_size=page_size,
#     )


# @router.get("/{doc_id}", response_model=DocumentInfo)
# async def get_document(
#     doc_id: str,
#     service=Depends(get_document_service),
# ) -> DocumentInfo:
#     return service.get_document(doc_id)


# @router.delete("/{doc_id}")
# async def delete_document(
#     doc_id: str,
#     ingest_service=Depends(get_ingest_service),
# ) -> dict:
#     ingest_service.delete_doc(doc_id)
#     return {"doc_id": doc_id, "deleted": True}
