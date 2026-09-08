def register(ctx):
    from hermes_cli.general_voice import run_workflow, before_tool, after_tool, system_section
    ctx.register_system_prompt_section('general-voice.delivery',system_section,max_chars=2400)
    ctx.register_hook('run_turn_workflow',run_workflow)
    ctx.register_hook('pre_tool_call',before_tool)
    ctx.register_hook('post_tool_call',after_tool)
