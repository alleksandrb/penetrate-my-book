import sys
from pathlib import Path

from src.books import BookDecodeError, add_book, sync_book
from src.report import create_csv


def _report_sync(name: str) -> None:
    def progress(index: int, total: int, word: str) -> None:
        print(f"{index}/{total} {word}", flush=True)

    result = sync_book(name, on_progress=progress)
    if result.status == "empty":
        print("nothing to fetch")
        return
    if result.status == "rate_limit":
        print("rate limit reached")
    elif result.status == "error":
        print(result.detail)
    if result.status == "done" or result.saved:
        print(f"saved {result.saved}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "--parse" and len(sys.argv) in (3, 4):
        source = Path(sys.argv[2])
        language = sys.argv[3] if len(sys.argv) == 4 else "en"
        try:
            print(add_book(source.name, source.read_bytes(), language))
        except BookDecodeError:
            raise SystemExit("file must be utf-8 or windows-1251")
        return
    if command == "--synk" and len(sys.argv) == 3:
        _report_sync(sys.argv[2])
        return
    if command == "--create-csv" and len(sys.argv) == 3:
        print(create_csv(sys.argv[2]))
        return
    if command == "--desktop" and len(sys.argv) == 2:
        from src.desktop import run

        run()
        return
    sys.exit(
        "Usage:\n"
        f"  python {Path(__file__).name} --parse <file.txt> [language]\n"
        f"  python {Path(__file__).name} --synk <table>\n"
        f"  python {Path(__file__).name} --create-csv <table>\n"
        f"  python {Path(__file__).name} --desktop"
    )


if __name__ == "__main__":
    main()
