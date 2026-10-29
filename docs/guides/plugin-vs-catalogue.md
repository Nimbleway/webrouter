# Plugin vs catalogue

Register a backend at runtime for your own process, or submit it so it ships in the package. Catalogue steps are [Adding a provider](../../CONTRIBUTING.md#adding-a-provider). A runtime example is [`examples/custom_provider.py`](../../examples/custom_provider.py).

| Aspect | Runtime registration | Catalogue submission |
| --- | --- | --- |
| Visibility | Private to your application | Available to every web-router install |
| Installation | You install its dependencies | The extra that installs that backend (`search`, `extract`, `research`, `browser`, or `aws`) |
| Reference | String name only | `Provider` enum + string |
| Configuration | Your choice | Environment variables + `provider_config` |
| Maintenance | Your responsibility | Maintained with web-router |
| Distribution | Your code | Shipped with the package |
| Time to use | Immediate | After merge, on the next `git pull && make setup` |
| Use case | Internal, experimental, custom | Generally available APIs, vendor-supported |

Promoting a shared option on a backend that already exists is [Promoting a provider option](../../CONTRIBUTING.md#promoting-a-provider-option). Wire fields live in [`contracts/`](../../contracts/) and are regenerated with `make contracts`.
