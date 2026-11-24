"""Index README.md and docs/**/*.md in the Workbench Solr core."""
import argparse
import hashlib
import re
from pathlib import Path

from .config import Config
from .rag import CHUNK_CHARS, CORPUS_ID, EMBEDDING_MODEL, SolrRetriever, embed
from .store import digest

HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def markdown_chunks(path, markdown):
    heading = Path(path).stem
    fence = ""
    lines = []
    size = 0
    start = 0
    start_offset = 0

    def passage():
        text = "\n".join(lines).rstrip()
        key = hashlib.sha256(f"{path}\0{start}\0{start_offset}\0{text}".encode()).hexdigest()
        return {"id": f"doc-{key}", "kind": "chunk", "path": path, "heading": heading,
                "line": start, "text": text}

    for number, line in enumerate(markdown.splitlines(), start=1):
        marker = FENCE.match(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
        elif marker:
            fence = marker[1]
        elif match := HEADING.match(line):
            if lines:
                yield passage()
                lines.clear()
                size = 0
            heading = match[1]
        for offset in range(0, max(len(line), 1), CHUNK_CHARS):
            fragment = line[offset:offset + CHUNK_CHARS]
            if size + len(fragment) + 1 > CHUNK_CHARS and lines:
                yield passage()
                lines.clear()
                size = 0
            if not lines:
                if not fragment.strip():
                    continue
                start = number
                start_offset = offset
            size += len(fragment) + bool(lines)
            lines.append(fragment)
    if lines:
        yield passage()


def build_corpus(root, config):
    root = root.resolve()
    paths = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    chunks = [
        chunk
        for path in paths
        if path.is_file() and path.resolve().is_relative_to(root)
        for chunk in markdown_chunks(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8-sig"))
    ]
    if not chunks:
        raise ValueError("No Markdown passages found in README.md or docs/")
    with_vectors = config.rag.method != "keyword"
    model = EMBEDDING_MODEL if with_vectors else "none"
    if with_vectors:
        for offset in range(0, len(chunks), 16):
            batch = chunks[offset:offset + 16]
            vectors = embed(
                config.ollama_url,
                [f"{chunk['heading']}\n{chunk['text']}" for chunk in batch],
                max(60, config.rag.timeout_seconds),
                keep_alive=0 if offset + len(batch) == len(chunks) else "5m",
            )
            for chunk, vector in zip(batch, vectors, strict=True):
                chunk["embedding"] = vector
    snapshot = digest({"model": model, "chunks": chunks})
    manifest = {"id": CORPUS_ID, "kind": "corpus", "snapshot": snapshot, "embedding_model": model}
    for chunk in chunks:
        chunk["snapshot"] = snapshot
    SolrRetriever(config.rag).replace_corpus(manifest, chunks)
    return len(chunks), snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Workbench config file")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Directory containing the Markdown corpus")
    args = parser.parse_args()
    try:
        config = Config.model_validate_json(args.config.read_text(encoding="utf-8-sig")) if args.config else Config()
        count, snapshot = build_corpus(args.root, config)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Indexing failed: {exc}\n")
    print(f"Indexed {count} passages in {config.rag.core} (snapshot {snapshot}).")


if __name__ == "__main__":
    main()
