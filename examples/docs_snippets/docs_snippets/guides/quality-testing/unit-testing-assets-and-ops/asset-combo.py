# start_file
import dagster as dg


class SeparatorConfig(dg.Config):
    separator: str


@dg.asset
def processed_file(
    primary_file: str, secondary_file: str, config: SeparatorConfig
) -> str:
    return f"{primary_file}{config.separator}{secondary_file}"


# end_file


# start_test
def test_processed_file() -> None:
    assert (
        processed_file(
            primary_file="abc",
            secondary_file="def",
            config=SeparatorConfig(separator=","),
        )
        == "abc,def"
    )


# end_test
