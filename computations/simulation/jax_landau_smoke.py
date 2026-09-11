from __future__ import annotations

import argparse
import json
from pathlib import Path

import jax
import jax.numpy as jnp


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a dimensionless JAX Landau-energy and autodiff smoke test."
    )
    parser.add_argument("--alpha", type=float, default=-1.0)
    parser.add_argument("--beta", type=float, default=1.0)
    parser.add_argument("--gamma", type=float, default=0.25)
    parser.add_argument("--field", type=float, default=0.0)
    parser.add_argument("--points", type=int, default=257)
    parser.add_argument("--p-max", type=float, default=1.5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.points < 3:
        raise SystemExit("--points must be at least 3")
    if args.p_max <= 0:
        raise SystemExit("--p-max must be positive")

    jax.config.update("jax_enable_x64", True)

    @jax.jit
    def energy_one(p: jax.Array) -> jax.Array:
        return args.alpha * p**2 + args.beta * p**4 + args.gamma * p**6 - args.field * p

    gradient_one = jax.grad(energy_one)
    grid = jnp.linspace(-args.p_max, args.p_max, args.points)
    energies = jax.jit(jax.vmap(energy_one))(grid)
    gradients = jax.jit(jax.vmap(gradient_one))(grid)
    minimum_index = int(jnp.argmin(energies))

    payload = {
        "status": "ok",
        "purpose": "runtime_smoke_only",
        "jax_version": jax.__version__,
        "backend": jax.default_backend(),
        "devices": [str(device) for device in jax.devices()],
        "model": "F(P)=alpha*P^2+beta*P^4+gamma*P^6-E*P",
        "units": "dimensionless_smoke_only",
        "parameters": {
            "alpha": args.alpha,
            "beta": args.beta,
            "gamma": args.gamma,
            "field": args.field,
        },
        "grid_points": args.points,
        "minimum": {
            "polarization": float(grid[minimum_index]),
            "energy": float(energies[minimum_index]),
            "gradient": float(gradients[minimum_index]),
        },
        "claim_boundary": (
            "This verifies JIT and automatic differentiation plumbing only. "
            "It is not a fitted HZO free-energy model."
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
