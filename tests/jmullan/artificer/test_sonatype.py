"""Tests for `jmullan.artificer.artificer`"""

import pytest

from jmullan.artificer import sonatype


def test_force_list():
    assert sonatype.force_a_list([]) == []
    assert sonatype.force_a_list(["foo"]) == ["foo"]

    assert sonatype.force_a_list(("foo",)) == ["foo"]

    assert sonatype.force_a_list({"foo"}) == ["foo"]

    assert sonatype.force_a_list("foo") == ["foo"]

    assert sonatype.force_a_list(7) == [7]
    assert sonatype.force_a_list(7.0) == [7.0]

    assert sonatype.force_a_list(b"foo") == ["foo"]

    assert sonatype.force_a_list(None) == []

    with pytest.raises(TypeError):
        sonatype.force_a_list({"foo": "bar"})


def test_load_json_or_python():
    assert sonatype.load_json_or_python("[]") == []
    assert sonatype.load_json_or_python('["foo"]') == ["foo"]
    assert sonatype.load_json_or_python("['foo']") == ["foo"]
    assert sonatype.load_json_or_python("7") == 7
    assert sonatype.load_json_or_python("null") is None
    assert sonatype.load_json_or_python("None") is None


def test_repo_validation():
    repo = sonatype.Repo("foo", "foo", "foo")
    assert repo is not None

    with pytest.raises(ValueError):  # noqa: PT011
        sonatype.Repo(None, "foo", "foo")

    with pytest.raises(ValueError):  # noqa: PT011
        sonatype.Repo("foo", None, "foo")

    with pytest.raises(ValueError):  # noqa: PT011
        sonatype.Repo("foo", "foo", None)
