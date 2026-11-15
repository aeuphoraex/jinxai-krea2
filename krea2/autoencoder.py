import torch
from einops import rearrange
from torch import Tensor, nn


class QwenAutoencoder(nn.Module):
    """qwen-ae-f8-16c: the Qwen-Image VAE (f8, 16 latent channels)."""

    def __init__(self):
        super().__init__()
        from diffusers import AutoencoderKLQwenImage

        self.ae = AutoencoderKLQwenImage.from_pretrained("Qwen/Qwen-Image", subfolder="vae")
        self.compression = 8
        self.channels = 16
        self.register_buffer("latents_mean", torch.tensor(self.ae.latents_mean).view(1, -1, 1, 1, 1))
        self.register_buffer("latents_std", torch.tensor(self.ae.latents_std).view(1, -1, 1, 1, 1))

    def decode(self, x: Tensor) -> Tensor:
        x = rearrange(x, "b c h w -> b c 1 h w")
        x = (x * self.latents_std) + self.latents_mean
        return rearrange(self.ae.decode(x).sample, "b c 1 h w -> b c h w")

    @torch.no_grad()
    def encode(self, image: Tensor) -> Tensor:
        """Pixels (b, 3, H, W) in [-1, 1] -> normalised latent (b, 16, H/8, W/8).

        Inverse of `decode`; this is what makes img2img possible without a second
        VAE implementation.
        """
        x = rearrange(image, "b c h w -> b c 1 h w").to(self.ae.dtype)
        z = self.ae.encode(x).latent_dist.sample()
        z = rearrange(z, "b c 1 h w -> b c h w")
        mean = self.latents_mean.view(1, -1, 1, 1).to(z.dtype)
        std = self.latents_std.view(1, -1, 1, 1).to(z.dtype)
        return (z - mean) / std
