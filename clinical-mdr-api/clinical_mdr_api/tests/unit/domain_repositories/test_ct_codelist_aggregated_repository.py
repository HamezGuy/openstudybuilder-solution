"""
Unit tests for CT Codelist Aggregated Repository utility functions
"""

from clinical_mdr_api.repositories._utils import escape_lucene_special_chars


class TestEscapeLuceneSpecialChars:
    """Tests for the escape_lucene_special_chars utility function"""

    def test_escape_square_brackets(self):
        """Test that square brackets are escaped"""
        assert escape_lucene_special_chars("[test]") == "\\[test\\]"
        assert escape_lucene_special_chars("a[b]c") == "a\\[b\\]c"

    def test_escape_curly_braces(self):
        """Test that curly braces are escaped"""
        assert escape_lucene_special_chars("{test}") == "\\{test\\}"
        assert escape_lucene_special_chars("a{b}c") == "a\\{b\\}c"

    def test_escape_parentheses(self):
        """Test that parentheses are escaped"""
        assert escape_lucene_special_chars("(test)") == "\\(test\\)"
        assert escape_lucene_special_chars("a(b)c") == "a\\(b\\)c"

    def test_escape_plus_minus(self):
        """Test that plus and minus signs are escaped"""
        assert escape_lucene_special_chars("+test") == "\\+test"
        assert escape_lucene_special_chars("-test") == "\\-test"
        assert escape_lucene_special_chars("a+b-c") == "a\\+b\\-c"

    def test_escape_exclamation(self):
        """Test that exclamation marks are escaped"""
        assert escape_lucene_special_chars("!test") == "\\!test"

    def test_escape_colon(self):
        """Test that colons are escaped"""
        assert escape_lucene_special_chars("field:value") == "field\\:value"

    def test_escape_tilde(self):
        """Test that tildes are escaped"""
        assert escape_lucene_special_chars("test~2") == "test\\~2"

    def test_escape_caret(self):
        """Test that carets are escaped"""
        assert escape_lucene_special_chars("test^2") == "test\\^2"

    def test_escape_quotes(self):
        """Test that double quotes are escaped"""
        assert escape_lucene_special_chars('"exact phrase"') == '\\"exact phrase\\"'

    def test_escape_asterisk_question(self):
        """Test that wildcards are escaped"""
        assert escape_lucene_special_chars("test*") == "test\\*"
        assert escape_lucene_special_chars("test?") == "test\\?"

    def test_escape_backslash(self):
        """Test that backslashes are escaped"""
        assert escape_lucene_special_chars("test\\value") == "test\\\\value"

    def test_escape_slash(self):
        """Test that forward slashes are escaped"""
        assert escape_lucene_special_chars("test/value") == "test\\/value"

    def test_escape_ampersand(self):
        """Test that ampersands are escaped (for && operator)"""
        assert escape_lucene_special_chars("a&b") == "a\\&b"
        assert escape_lucene_special_chars("a&&b") == "a\\&\\&b"

    def test_escape_pipe(self):
        """Test that pipes are escaped (for || operator)"""
        assert escape_lucene_special_chars("a|b") == "a\\|b"
        assert escape_lucene_special_chars("a||b") == "a\\|\\|b"

    def test_escape_multiple_special_chars(self):
        """Test escaping multiple special characters in one string"""
        input_str = "[test]{value}(group)+required-prohibited"
        expected = "\\[test\\]\\{value\\}\\(group\\)\\+required\\-prohibited"
        assert escape_lucene_special_chars(input_str) == expected

    def test_escape_empty_string(self):
        """Test that empty strings are handled"""
        assert escape_lucene_special_chars("") == ""

    def test_escape_no_special_chars(self):
        """Test that strings without special chars are unchanged"""
        assert escape_lucene_special_chars("simple text") == "simple text"
        assert escape_lucene_special_chars("ABC123") == "ABC123"

    def test_escape_preserves_normal_chars(self):
        """Test that normal characters are preserved"""
        assert escape_lucene_special_chars("Test Value 123") == "Test Value 123"
        assert (
            escape_lucene_special_chars("alpha-numeric_text") == "alpha\\-numeric_text"
        )

    def test_realistic_search_strings(self):
        """Test realistic search strings that might contain special chars"""
        # Email-like pattern
        assert escape_lucene_special_chars("user@domain.com") == "user@domain.com"

        # Version string
        assert escape_lucene_special_chars("v1.2.3") == "v1.2.3"

        # Chemical formula (often contains brackets and plus/minus)
        assert escape_lucene_special_chars("Ca[2+]") == "Ca\\[2\\+\\]"

        # Query with range brackets
        assert escape_lucene_special_chars("[A TO Z]") == "\\[A TO Z\\]"
