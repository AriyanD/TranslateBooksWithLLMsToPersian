"""Command line: python translate.py book.epub -p providers.json"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .dispatcher import DispatchSettings
from .job import TranslationJob
from .providers import load_providers_file


def _watch_providers(path: str, job: TranslationJob, stop: threading.Event):
    """While the job runs, re-read the providers file whenever it is saved, so you
    can add a provider or an extra key mid-translation (e.g. when a key runs dry)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return
    while not stop.wait(3):
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if m == mtime:
            continue
        mtime = m
        try:
            notes = job.add_providers(load_providers_file(path))
            print(f"[providers file changed] {'; '.join(notes) if notes else 'nothing new'}")
        except Exception as e:
            print(f"[providers file changed] could not read it: {e}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Translate a book/subtitle/document into Persian "
                                             "using several API providers in parallel.")
    ap.add_argument("input", help=".epub, .docx, .pdf, .txt, .md or .srt file")
    ap.add_argument("-p", "--providers", default="providers.json",
                    help="JSON file with your providers (see providers.example.json)")
    ap.add_argument("-o", "--output", help="output path (default: <name>.fa.<ext>)")
    ap.add_argument("--chunk-chars", type=int, default=2500, help="characters per API request")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--timeout", type=float, default=240, help="seconds per request")
    ap.add_argument("--max-attempts", type=int, default=6, help="tries per chunk across providers")
    ap.add_argument("--instructions", default="", help="extra instructions (tone, glossary...)")
    ap.add_argument("--persian-digits", action="store_true", help="convert 0-9 to ۰-۹")
    ap.add_argument("--no-resume", action="store_true", help="ignore saved progress")
    ap.add_argument("--refine", action="store_true",
                    help="the input is ALREADY in Persian: polish/refine it instead of translating")
    ap.add_argument("--no-wait", action="store_true",
                    help="stop (instead of pausing) when every API key is dead or out of quota")
    args = ap.parse_args(argv)

    if not os.path.exists(args.providers):
        sys.exit(f"Providers file not found: {args.providers}\n"
                 f"Copy providers.example.json to providers.json and fill in your APIs.")
    providers = load_providers_file(args.providers)
    mode = "refine" if args.refine else "translate"
    usable = [p for p in providers if p.used_for(mode)]
    if not usable:
        sys.exit(f"No provider in {args.providers} is set to be used for "
                 f"{'review' if args.refine else 'translation'} "
                 f"(check the \"use_for\" field of each provider).")
    skipped = [p.name for p in providers if p not in usable]
    if skipped:
        print(f"Not used for this job (use_for): {', '.join(skipped)}")
    with open(args.input, "rb") as f:
        data = f.read()
    settings = DispatchSettings(max_chars=args.chunk_chars, max_attempts=args.max_attempts,
                                temperature=args.temperature, timeout=args.timeout,
                                extra_instructions=args.instructions,
                                persian_digits=args.persian_digits,
                                wait_for_api=not args.no_wait,
                                mode=mode)
    job = TranslationJob(os.path.basename(args.input), data, providers, settings,
                         log=print, use_checkpoint=not args.no_resume)
    stop = threading.Event()
    threading.Thread(target=_watch_providers, args=(args.providers, job, stop), daemon=True).start()
    try:
        out = job.run()
    except KeyboardInterrupt:
        job.cancel.set()
        print("Interrupted. Progress is saved, run the same command again to resume.")
        return 130
    finally:
        stop.set()
    if out is None:
        return 1
    path = args.output or os.path.join(os.path.dirname(os.path.abspath(args.input)), job.output_name)
    with open(path, "wb") as f:
        f.write(out)
    print(f"Saved: {path}")
    if job.dispatcher:
        for p in job.dispatcher.stats()["providers"]:
            print(f"  {p['name']:<20} chunks: {p['done']:<5} errors: {p['failed']:<4} {p['status']}")
    return 0 if job.status == "done" else 2


if __name__ == "__main__":
    sys.exit(main())
