import pytest
from utils.code import extract_code, extract_error_section, format_error_for_query, summarize_error


class TestExtractCode:
    def test_none_for_empty_input(self):
        assert extract_code("") is None
        assert extract_code("   ") is None
        assert extract_code(None) is None

    def test_extract_from_python_fence(self):
        text = """Spiegazione...
```python
import bpy
bpy.ops.mesh.primitive_cube_add()
```
Fine."""
        result = extract_code(text)
        assert result == "import bpy\nbpy.ops.mesh.primitive_cube_add()"

    def test_extract_from_bare_fence(self):
        text = """```
import bpy
obj = bpy.data.objects["Cube"]
```"""
        result = extract_code(text)
        assert "import bpy" in result
        assert "bpy.data.objects" in result

    def test_extract_inline_code(self):
        text = 'Use `import bpy; bpy.ops.mesh.primitive_cube_add(size=2)` to create.'
        result = extract_code(text)
        assert result == "import bpy; bpy.ops.mesh.primitive_cube_add(size=2)"

    def test_extract_lines_starting_with_import_bpy(self):
        text = """Ecco lo script:
import bpy
from bpy import context
bpy.ops.mesh.primitive_cube_add(size=2)"""
        result = extract_code(text)
        assert "import bpy" in result
        assert "bpy.ops.mesh" in result

    def test_does_not_extract_from_comment(self):
        text = """# this is for bpy.ops but it's a comment
x = 1"""
        result = extract_code(text)
        assert result is None

    def test_returns_none_when_no_bpy_code(self):
        text = "Hello, this does not contain Blender Python code."
        result = extract_code(text)
        assert result is None

    def test_extracts_from_raw_text_with_bpy_dot(self):
        text = "bpy.ops.object.select_all(action='SELECT')"
        result = extract_code(text)
        assert "bpy.ops.object" in result


class TestExtractErrorSection:
    def test_extracts_traceback(self):
        output = """Blender 3.0
Read blend: file.blend
Traceback (most recent call last):
  File "script.py", line 10, in <module>
    obj = bpy.data.objects["Missing"]
KeyError: 'Missing'
Error: Blender quit"""
        result = extract_error_section(output, max_lines=10)
        assert "Traceback" in result
        assert "KeyError" in result

    def test_returns_last_lines_when_no_traceback(self):
        output = "line1\nline2\nline3\nline4\nline5\nline6"
        result = extract_error_section(output, max_lines=3)
        assert result == "\n".join(["line4", "line5", "line6"])

    def test_respects_max_lines(self):
        lines = ["line"] * 100
        output = "\n".join(lines)
        result = extract_error_section(output, max_lines=10)
        assert len(result.splitlines()) == 10


class TestFormatErrorForQuery:
    def test_extracts_key_lines(self):
        error = """line1
Error: name 'x' is not defined
line3
Warning: something
line5"""
        result = format_error_for_query(error)
        assert "Error: name 'x' is not defined" in result
        assert "Warning: something" in result

    def test_fallback_to_last_lines(self):
        error = "line1\nline2\nline3\nline4"
        result = format_error_for_query(error)
        assert "line2" in result


class TestSummarizeError:
    def test_returns_unchanged_when_short(self):
        error = "Error: test"
        result = summarize_error(error, max_len=100)
        assert result == error

    def test_truncates_from_end(self):
        error = "short" * 100
        result = summarize_error(error, max_len=50)
        assert len(result) == 50
        assert result == error[-50:]
