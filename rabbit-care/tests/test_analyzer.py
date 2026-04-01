import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch, MagicMock
import numpy as np

def test_analyze_frames_returns_eating():
    """Gemini 回傳 eating 時，函式應回傳 ('eating', 0.92)"""
    from motion_watcher import analyze_frames

    mock_response = MagicMock()
    mock_response.text = '{"action": "eating", "confidence": 0.92}'

    fake_frames = [np.zeros((100, 100, 3), dtype=np.uint8)] * 4
    fake_pil = [MagicMock()] * 4

    with patch('motion_watcher._get_gemini_client') as mock_client, \
         patch('motion_watcher.frames_to_pil', return_value=fake_pil):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_client.return_value.models = MagicMock()
        mock_client.return_value.models.generate_content = mock_model.generate_content
        result = analyze_frames(fake_frames)

    assert result == ('eating', 0.92)

def test_analyze_frames_filters_low_confidence():
    """信心值低於 0.7 應回傳 None"""
    from motion_watcher import analyze_frames

    mock_response = MagicMock()
    mock_response.text = '{"action": "eating", "confidence": 0.5}'

    fake_frames = [np.zeros((100, 100, 3), dtype=np.uint8)] * 4
    fake_pil = [MagicMock()] * 4

    with patch('motion_watcher._get_gemini_client') as mock_client, \
         patch('motion_watcher.frames_to_pil', return_value=fake_pil):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_client.return_value.models.generate_content = mock_model.generate_content
        result = analyze_frames(fake_frames)

    assert result is None

def test_analyze_frames_filters_other():
    """action == 'other' 應回傳 None"""
    from motion_watcher import analyze_frames

    mock_response = MagicMock()
    mock_response.text = '{"action": "other", "confidence": 0.95}'

    fake_frames = [np.zeros((100, 100, 3), dtype=np.uint8)] * 4
    fake_pil = [MagicMock()] * 4

    with patch('motion_watcher._get_gemini_client') as mock_client, \
         patch('motion_watcher.frames_to_pil', return_value=fake_pil):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_client.return_value.models.generate_content = mock_model.generate_content
        result = analyze_frames(fake_frames)

    assert result is None
