"""Existing resources only. Supply mailbox_id and proxy_id through the input JSON."""

from pydantic import BaseModel, Field

from mobilerun_functions import (
    Context,
    Credentials,
    Device,
    Mailbox,
    Operator,
    Proxy,
    binding,
    script,
)


class Input(BaseModel):
    mailbox_id: str = Field(json_schema_extra={"format": "mobilerun-mailbox"})
    proxy_id: str = Field(json_schema_extra={"format": "mobilerun-proxy"})
    package: str = "com.example.app"


class Output(BaseModel):
    success: bool


class Ctx(Context):
    device: Device
    credentials: Credentials = binding(package="com.example.app")
    mailbox: Mailbox = binding(input="mailbox_id")
    proxy: Proxy = binding(input="proxy_id")
    operator: Operator


@script
async def main(input: Input, ctx: Ctx) -> Output:
    await ctx.device.start_app(input.package)
    await ctx.device.do("Focus the password field", max_steps=5)
    await ctx.credentials.type_field(ctx.device, input.package, "password")
    code = await ctx.mailbox.wait_for_code()
    await ctx.device.input_text(code)
    return Output(success=True)
