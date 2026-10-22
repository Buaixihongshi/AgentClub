"""Apply a role's provider-specific model without mutating shared CLI config."""
from backend.app.domain.models import CLIProvider


def with_model(provider: CLIProvider, model: str) -> CLIProvider:
    if not model or provider.id not in {"codex", "claude", "antigravity"}:
        return provider
    args = []
    source = iter(provider.args)
    for arg in source:
        if arg in {"--model", "-m"}:
            next(source, None)
        elif not arg.startswith(("--model=", "-m=")):
            args.append(arg)
    # Place options before -p, which some CLIs use as a prompt delimiter.
    index = args.index("-p") if "-p" in args else len(args)
    args[index:index] = ["--model", model]
    return provider.model_copy(update={"args": args})
