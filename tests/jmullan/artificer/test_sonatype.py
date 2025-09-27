"""Tests for `jmullan.artificer.artificer`"""
from jmullan.artificer import sonatype
import pytest

def test_force_list():
    assert [] == sonatype.force_a_list([])
    assert ["foo"] == sonatype.force_a_list(["foo"])

    assert ["foo"] == sonatype.force_a_list(("foo",))

    assert ["foo"] == sonatype.force_a_list({"foo"})

    assert ["foo"] == sonatype.force_a_list("foo")

    assert [7] == sonatype.force_a_list(7)
    assert [7.0] == sonatype.force_a_list(7.0)

    assert ["foo"] == sonatype.force_a_list(b"foo")

    assert [] == sonatype.force_a_list(None)

    with pytest.raises(TypeError):
        sonatype.force_a_list({"foo": "bar"})


def test_load_json_or_python():
    assert [] == sonatype.load_json_or_python("[]")
    assert ["foo"] == sonatype.load_json_or_python('["foo"]')
    assert ["foo"] == sonatype.load_json_or_python("['foo']")
    assert 7 == sonatype.load_json_or_python("7")
    assert None == sonatype.load_json_or_python("null")
    assert None == sonatype.load_json_or_python("None")


def test_repo_validation():
    repo = sonatype.Repo("foo", "foo", "foo")
    assert repo is not None

    with pytest.raises(ValueError):
        sonatype.Repo(None, "foo", "foo")

    with pytest.raises(ValueError):
        sonatype.Repo("foo", None, "foo")

    with pytest.raises(ValueError):
        sonatype.Repo("foo", "foo", None)
