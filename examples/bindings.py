"""Existing resources only. The run selects them: --mailbox-id, --proxy-kind and --proxy-id."""

from pydantic import BaseModel

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
    package: str = "com.example.app"


class Output(BaseModel):
    success: bool


class Confirmation(BaseModel):
    confirmed: bool


class Ctx(Context):
    device: Device
    credentials: Credentials = binding(package="com.example.app")
    mailbox: Mailbox
    proxy: Proxy
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
