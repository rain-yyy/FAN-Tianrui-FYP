from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

def load_and_split_docs(file_paths: list[str]) -> list[Document]:
    """
    加载文件内容并将其切分为文本块。
    """
    docs = []
    
    # 建议的文本分割器，它会尝试按代码的逻辑结构（类、函数等）来分割
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,
        chunk_overlap=200,

    )

    for file_path in file_paths:
        try:
            # 使用TextLoader加载每个文件
            loader = TextLoader(file_path, encoding='utf-8')
            file_docs = loader.load_and_split(text_splitter=text_splitter)
            docs.extend(file_docs)
        except Exception as e:
            print(f"Skipping file {file_path} due to error: {e}")
            continue
            
    print(f"Split content into {len(docs)} document chunks.")
    return docs