"""The starter script `python -m mobilerun_functions new` writes."""

STARTER_SCRIPT = '''\
from pydantic import BaseModel
from mobilerun_functions import Context, script


class Input(BaseModel):
    name: str
    greeting: str = "Hello"


class Output(BaseModel):
    message: str


class Ctx(Context):
    pass


@script
def main(input: Input, ctx: Ctx) -> Output:
    ctx.log.info(f"greeting {input.name}")
    return Output(message=f"{input.greeting}, {input.name}!")
'''
