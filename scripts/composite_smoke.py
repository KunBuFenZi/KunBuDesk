from pathlib import Path
import yaml

path = Path(".generated/smoke")
path.mkdir(parents=True, exist_ok=True)
action = {
    "name": "Runtime composite smoke test", "description": "Verify local manifests generated during a preceding step",
    "runs": {"using": "composite", "steps": [
        {"name": "Check inherited environment", "shell": "bash", "run": 'test "$CB_SMOKE" = runtime-action-ok'},
        {"name": "Check nested action expressions", "uses": "actions/github-script@d7906e4ad0b1822421a7e6a35d5ca353c962f410",
         "with": {"script": "if (process.env.CB_SMOKE !== 'runtime-action-ok') core.setFailed('Missing inherited env'); core.info('Runtime-generated composite actions work');"}},
    ]},
}
(path / "action.yml").write_text(yaml.safe_dump(action, sort_keys=False), encoding="utf-8")
