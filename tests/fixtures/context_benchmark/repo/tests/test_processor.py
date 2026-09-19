from src.processor import Processor


def test_process_normalizes_value():
    assert Processor().process(" HELLO ") == "hello"
