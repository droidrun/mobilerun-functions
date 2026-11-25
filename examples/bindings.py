"""Existing resources only. Supply mailbox_id and proxy_id through the input JSON."""

from pydantic import BaseModel, Field

from mobilerun_functions import (
    AI,
    Context,
    Credentials,
    Device,
    Mailbox,
    Proxy,
    Tasks,
    binding,
    script,
)


class Input(BaseModel):
    mailbox_id: str = Field(json_schema_extra={"format": "mobilerun-mailbox"})
    proxy_id: str = Field(json_schema_extra={"format": "mobilerun-proxy"})
    package: str = "com.example.app"


class Output(BaseModel):
    success: bool


class Confirmation(BaseModel):
    confirmed: bool


class Ctx(Context):
    device: Device
    credentials: Credentials = binding(package="com.example.app")
    mailbox: Mailbox = binding(input="mailbox_id")
    proxy: Proxy = binding(input="proxy_id")
    tasks: Tasks
    ai: AI


@script
async def main(input: Input, ctx: Ctx) -> Output:
    await ctx.device.start_app(input.package)
    await ctx.tasks.run("Focus the password field", device=ctx.device, max_steps=5)
    await ctx.credentials.type_field(ctx.device, "password")
    reply = await ctx.ai.openai().chat.completions.create(
        model="openai/gpt-4o",
        messages=[{"role": "user", "content": "Say hello in one word"}],
    )
    ctx.log.info(reply.choices[0].message.content)
    code = await ctx.mailbox.wait_for_code()
    await ctx.device.input_text(code)
    check = await ctx.tasks.run(
        "Check whether the confirmation completed successfully",
        device=ctx.device,
        output=Confirmation,
    )
    return Output(success=check.success and check.output is not None and check.output.confirmed)
