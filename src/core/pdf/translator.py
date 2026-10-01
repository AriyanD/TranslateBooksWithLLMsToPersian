"""
PDF translation entry point.

Wires the PDF extractor, the shared plain-text pipeline and the PDF builder:

    input.pdf -> extract_pdf_paragraphs -> translate_paragraphs_plain -> build_pdf -> output.pdf

This mirrors ``DocxTranslationAdapter._translate_plain_text`` without the
``GenericTranslationOrchestrator`` layer: PDF has no placeholder path, so the
orchestrator would be dead weight. Segment-level checkpointing reuses the
plain-text partial-state contract unchanged, with the DOCX ``file_href``
convention (the input file's basename).
"""

import os
from typing import Any, Callable, Dict, Optional

from src.core.common.plain_text_checkpoint import (
    build_plain_checkpoint_hook,
    delete_plain_checkpoint,
    resume_plain_segments,
)
from src.core.common.plain_text_pipeline import translate_paragraphs_plain
from src.core.pdf.builder import build_pdf
from src.core.pdf.content import PdfError
from src.core.pdf.extractor import extract_pdf_paragraphs


async def translate_pdf_file(
    input_filepath: str,
    output_filepath: str,
    source_language: str,
    target_language: str,
    model_name: str,
    llm_client: Any,
    max_tokens_per_chunk: int = 450,
    log_callback: Optional[Callable] = None,
    stats_callback: Optional[Callable] = None,
    prompt_options: Optional[Dict] = None,
    max_retries: int = 1,
    context_manager: Optional[Any] = None,
    check_interruption_callback: Optional[Callable] = None,
    checkpoint_manager: Optional[Any] = None,
    translation_id: Optional[str] = None,
    parallel_workers: int = 1,
    **kwargs,
) -> Dict[str, Any]:
    """
    Translate a text PDF into a new, reflowed PDF.

    Steps:
    1. Load a plain-text partial state for this file when checkpointing is on
    2. Extract paragraphs, styles and images (PyMuPDF)
    3. Translate the paragraphs through the shared plain-text pipeline,
       resuming at the first unattempted segment when a state exists
    4. Rebuild a PDF from the translated paragraphs and drop the checkpoint

    Args:
        input_filepath: Input PDF file path
        output_filepath: Output PDF file path
        source_language: Source language name
        target_language: Target language name
        model_name: LLM model name
        llm_client: LLM client instance
        max_tokens_per_chunk: Max tokens per chunk
        log_callback: Logging callback function, called as (key, message)
        stats_callback: Statistics callback function (called after each chunk)
        prompt_options: Prompt options (``bilingual`` is honoured by the builder)
        max_retries: Max translation retries (recorded in the checkpoint)
        context_manager: Adaptive context manager (optional)
        check_interruption_callback: Callback to check for interruption (optional)
        checkpoint_manager: Checkpoint manager for partial state (optional)
        translation_id: Translation ID for checkpointing (optional)
        parallel_workers: Number of concurrent chunk translations
        **kwargs: Additional arguments (ignored)

    Returns:
        Dict with success, stats, output_path (and ``interrupted`` when the
        translation was interrupted; no output file is written in that case
        and the checkpoint is kept for a later resume).

    Raises:
        PdfError: A subclass (PdfOpenError, PdfEncryptedError,
            PdfNoTextLayerError) when the input cannot be extracted, or
            PdfBuildError when the output cannot be generated. No output file
            is written on an extraction failure.
    """
    # Use filename as file_href for checkpointing (DOCX convention)
    file_href = os.path.basename(input_filepath)
    bilingual = bool(prompt_options.get('bilingual')) if prompt_options else False

    # Check for resume state
    resume_state = None
    if checkpoint_manager and translation_id:
        resume_state = checkpoint_manager.load_xhtml_partial_state(translation_id, file_href)

    try:
        content = extract_pdf_paragraphs(input_filepath)
    except PdfError as exc:
        if log_callback:
            log_callback("pdf_extraction_failed", f"❌ {exc}")
        raise

    paragraph_count = len(content.paragraphs_text)
    n_images = sum(len(images) for images in content.images_by_paragraph.values())

    if log_callback:
        log_callback(
            "pdf_extracted",
            f"📄 PDF: {content.page_count} pages, {paragraph_count} paragraphs, "
            f"{len(content.tables)} tables, {n_images} images"
        )

    resume_segments, resume_translated = resume_plain_segments(
        resume_state, paragraph_count, log_callback
    )
    checkpoint_hook = build_plain_checkpoint_hook(
        checkpoint_manager=checkpoint_manager,
        translation_id=translation_id,
        file_href=file_href,
        source_language=source_language,
        target_language=target_language,
        model_name=model_name,
        max_tokens_per_chunk=max_tokens_per_chunk,
        max_retries=max_retries,
        paragraph_count=paragraph_count,
        prompt_options=prompt_options,
        bilingual=bilingual,
    )

    translated, stats, was_interrupted = await translate_paragraphs_plain(
        paragraphs=content.paragraphs_text,
        source_language=source_language,
        target_language=target_language,
        model_name=model_name,
        llm_client=llm_client,
        max_tokens_per_chunk=max_tokens_per_chunk,
        log_callback=log_callback,
        stats_callback=stats_callback,
        context_manager=context_manager,
        check_interruption_callback=check_interruption_callback,
        prompt_options=prompt_options,
        parallel_workers=parallel_workers,
        resume_segments=resume_segments,
        resume_translated=resume_translated,
        checkpoint_hook=checkpoint_hook,
    )

    if was_interrupted:
        if log_callback:
            log_callback("pdf_interrupted", "⏸️ PDF translation interrupted - state saved")
        return {
            'success': False,
            'stats': stats.to_dict(),
            'output_path': None,
            'interrupted': True,
        }

    build_pdf(
        translated,
        content,
        output_filepath,
        target_language,
        source_language,
        bilingual,
    )

    if log_callback:
        log_callback("pdf_rebuilt", f"📄 PDF rebuilt ({os.path.getsize(output_filepath)} bytes)")

    # The document is complete: drop the segment checkpoint so a later
    # resume of the same job does not replay a stale state.
    delete_plain_checkpoint(checkpoint_manager, translation_id, file_href, log_callback)

    return {
        'success': True,
        'stats': stats.to_dict(),
        'output_path': output_filepath,
    }
