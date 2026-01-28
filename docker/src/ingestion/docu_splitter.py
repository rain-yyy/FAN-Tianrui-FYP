from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from src.ingestion.ts_parser import TreeSitterParser
from pathlib import Path

def load_and_split_docs(file_paths: list[str]) -> list[Document]:
    """
    加载文件内容并将其切分为文本块。
    支持 Tree-sitter AST 感知切片（针对代码文件）和普通字符切片。
    """
    docs = []
    ts_parser = TreeSitterParser()
    
    # 普通文本分割器
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,
        chunk_overlap=200,
    )

    for file_path in file_paths:
        try:
            path_obj = Path(file_path)
            extension = path_obj.suffix.lower()
            
            # 检查是否为支持的代码文件
            if extension in TreeSitterParser.EXTENSION_TO_LANGUAGE:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                chunks = ts_parser.parse_code(content, extension)
                for chunk in chunks:
                    metadata = {
                        "source": file_path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "node_type": chunk.node_type,
                        "name": chunk.name
                    }
                    docs.append(Document(page_content=chunk.content, metadata=metadata))
            else:
                # 使用 TextLoader 加载非代码或不支持的代码文件
                loader = TextLoader(file_path, encoding='utf-8')
                file_docs = loader.load_and_split(text_splitter=text_splitter)
                docs.extend(file_docs)
                
        except Exception as e:
            # 获取更详细的错误信息
            import traceback
            error_details = traceback.format_exc().splitlines()[-1]
            print(f"Skipping file {file_path} due to error: {e} ({error_details})")
            continue
            
    print(f"Split content into {len(docs)} document chunks.")
    return docs
