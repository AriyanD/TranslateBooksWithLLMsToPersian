"""Tests for the PDF branch of the unified translate_file() router."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.adapters.translate_file import get_file_type_from_path, translate_file


def _checkpoint_manager_without_job():
    manager = Mock()
    manager.db.get_job.return_value = None
    return manager


async def _route(input_path, output_path, checkpoint_manager):
    return await translate_file(
        input_filepath=input_path,
        output_filepath=output_path,
        source_language="English",
        target_language="French",
        model_name="test-model",
        llm_provider="ollama",
        checkpoint_manager=checkpoint_manager,
        translation_id="job1",
        llm_api_endpoint="http://localhost:11434/api/generate",
    )


@pytest.mark.asyncio
async def test_pdf_routes_to_translate_pdf_file(simple_pdf_path, tmp_path):
    output = str(tmp_path / "out.pdf")
    manager = _checkpoint_manager_without_job()
    llm_client = Mock()
    pdf_mock = AsyncMock(return_value={'success': True, 'stats': {}, 'output_path': output})

    with patch("src.core.llm.create_llm_provider", return_value=llm_client) as create, \
            patch("src.core.pdf.translator.translate_pdf_file", pdf_mock):
        result = await _route(simple_pdf_path, output, manager)

    assert result is True
    create.assert_called_once()
    pdf_mock.assert_awaited_once()
    kwargs = pdf_mock.await_args.kwargs
    assert kwargs["input_filepath"] == simple_pdf_path
    assert kwargs["output_filepath"] == output
    assert kwargs["llm_client"] is llm_client
    assert kwargs["checkpoint_manager"] is manager
    assert kwargs["translation_id"] == "job1"
    # The parent checkpoint job is created for PDF, like EPUB/DOCX
    manager.start_job.assert_called_once()
    assert manager.start_job.call_args.kwargs["file_type"] == "pdf"


@pytest.mark.asyncio
async def test_pdf_interrupted_returns_false(simple_pdf_path, tmp_path):
    output = str(tmp_path / "out.pdf")
    pdf_mock = AsyncMock(return_value={
        'success': False, 'stats': {}, 'output_path': None, 'interrupted': True,
    })

    with patch("src.core.llm.create_llm_provider", return_value=Mock()), \
            patch("src.core.pdf.translator.translate_pdf_file", pdf_mock):
        result = await _route(simple_pdf_path, output, _checkpoint_manager_without_job())

    assert result is False
    pdf_mock.assert_awaited_once()


def test_get_file_type_from_path_pdf():
    assert get_file_type_from_path("a.PDF") == "pdf"
    assert get_file_type_from_path("dir/paper.pdf") == "pdf"
    # Existing mappings are unchanged
    assert get_file_type_from_path("a.docx") == "docx"
    assert get_file_type_from_path("a.epub") == "epub"
