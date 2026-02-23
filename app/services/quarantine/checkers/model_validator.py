"""Model file validator — validates model file formats for safety."""

import json
import struct
from pathlib import Path

from app.services.quarantine.stages import StageFinding

DANGEROUS_EXTENSIONS = {".pkl", ".pickle", ".bin", ".pt", ".pth", ".ckpt"}

ALLOWED_DTYPES = {
    "F16", "BF16", "F32", "F64",
    "I8", "I16", "I32", "I64",
    "U8", "U16", "U32", "U64",
    "BOOL",
}

KNOWN_ARCHITECTURES = {
    "LlamaForCausalLM",
    "MistralForCausalLM",
    "Qwen2ForCausalLM",
    "GPTNeoXForCausalLM",
    "GPT2LMHeadModel",
    "PhiForCausalLM",
    "GemmaForCausalLM",
    "Phi3ForCausalLM",
    "CohereForCausalLM",
    "StableLmForCausalLM",
    "InternLMForCausalLM",
    "BaichuanForCausalLM",
    "ChatGLMForCausalLM",
    "FalconForCausalLM",
    "MPTForCausalLM",
    "BloomForCausalLM",
    "OPTForCausalLM",
}

KNOWN_MODEL_TYPES = {
    "llama", "mistral", "qwen2", "gpt_neox", "gpt2",
    "phi", "phi3", "gemma", "gemma2", "cohere",
    "stablelm", "internlm", "baichuan", "chatglm",
    "falcon", "mpt", "bloom", "opt",
}

GGUF_MAGIC = b"GGUF"
MAX_HEADER_SIZE = 100 * 1024 * 1024  # 100MB


class ModelFileValidator:
    """Validates model files for safety and format correctness."""

    async def validate(
        self, file_path: Path, original_filename: str, config: dict
    ) -> list[StageFinding]:
        """Validate a model file.

        Returns findings for dangerous formats, invalid structures, or
        unknown architectures.
        """
        suffix = Path(original_filename).suffix.lower()

        # Dangerous pickle-based formats
        if suffix in DANGEROUS_EXTENSIONS:
            return [
                StageFinding(
                    stage="ai_safety",
                    severity="critical",
                    code="model_dangerous_format",
                    message=(
                        f"Dangerous model format '{suffix}' can contain "
                        f"arbitrary code via Python pickle deserialization."
                    ),
                    details={"extension": suffix, "filename": original_filename},
                )
            ]

        findings: list[StageFinding] = []

        if suffix == ".safetensors":
            findings.extend(self._validate_safetensors(file_path, original_filename))
        elif suffix == ".gguf":
            findings.extend(self._validate_gguf(file_path, original_filename))
        elif original_filename.endswith("config.json") or suffix == ".json":
            findings.extend(self._validate_model_config(file_path, original_filename))

        return findings

    def _validate_safetensors(
        self, file_path: Path, original_filename: str
    ) -> list[StageFinding]:
        """Deep validation of safetensors format."""
        findings: list[StageFinding] = []
        file_size = file_path.stat().st_size

        try:
            with open(file_path, "rb") as f:
                # Read header size (first 8 bytes, little-endian uint64)
                header_size_bytes = f.read(8)
                if len(header_size_bytes) < 8:
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="high",
                            code="model_invalid_header",
                            message="Safetensors file too small to contain a valid header.",
                            details={"filename": original_filename, "file_size": file_size},
                        )
                    )
                    return findings

                header_size = struct.unpack("<Q", header_size_bytes)[0]

                if header_size > MAX_HEADER_SIZE or header_size > file_size:
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="high",
                            code="model_invalid_header",
                            message=(
                                f"Safetensors header size ({header_size}) exceeds "
                                f"limits (max 100MB or file size {file_size})."
                            ),
                            details={
                                "filename": original_filename,
                                "header_size": header_size,
                                "file_size": file_size,
                            },
                        )
                    )
                    return findings

                # Parse header JSON
                header_bytes = f.read(header_size)
                try:
                    header = json.loads(header_bytes)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="high",
                            code="model_invalid_header",
                            message="Safetensors header is not valid JSON.",
                            details={"filename": original_filename},
                        )
                    )
                    return findings

                # Check metadata for suspicious entries
                metadata = header.get("__metadata__", {})
                if isinstance(metadata, dict):
                    suspicious_keys = []
                    for key in metadata:
                        key_lower = key.lower()
                        if any(
                            s in key_lower
                            for s in ("eval", "exec", "import", "os.", "subprocess", "system(")
                        ):
                            suspicious_keys.append(key)
                    if suspicious_keys:
                        findings.append(
                            StageFinding(
                                stage="ai_safety",
                                severity="medium",
                                code="model_suspicious_metadata",
                                message=(
                                    f"Suspicious metadata keys found: {suspicious_keys}"
                                ),
                                details={
                                    "filename": original_filename,
                                    "suspicious_keys": suspicious_keys,
                                },
                            )
                        )

                # Validate tensor entries
                for key, value in header.items():
                    if key == "__metadata__":
                        continue
                    if not isinstance(value, dict):
                        continue
                    dtype = value.get("dtype")
                    if dtype and dtype not in ALLOWED_DTYPES:
                        findings.append(
                            StageFinding(
                                stage="ai_safety",
                                severity="medium",
                                code="model_invalid_dtype",
                                message=f"Invalid dtype '{dtype}' for tensor '{key}'.",
                                details={
                                    "filename": original_filename,
                                    "tensor": key,
                                    "dtype": dtype,
                                },
                            )
                        )

        except OSError as e:
            findings.append(
                StageFinding(
                    stage="ai_safety",
                    severity="high",
                    code="model_invalid_header",
                    message=f"Failed to read safetensors file: {e}",
                    details={"filename": original_filename},
                )
            )

        return findings

    def _validate_gguf(
        self, file_path: Path, original_filename: str
    ) -> list[StageFinding]:
        """Basic GGUF format validation."""
        findings: list[StageFinding] = []

        try:
            with open(file_path, "rb") as f:
                magic = f.read(4)
                if magic != GGUF_MAGIC:
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="high",
                            code="model_invalid_gguf",
                            message=f"Invalid GGUF magic bytes: expected 'GGUF', got {magic!r}.",
                            details={"filename": original_filename, "magic": magic.hex()},
                        )
                    )
                    return findings

                version_bytes = f.read(4)
                if len(version_bytes) < 4:
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="high",
                            code="model_invalid_gguf",
                            message="GGUF file too small to contain version field.",
                            details={"filename": original_filename},
                        )
                    )
                    return findings

                version = struct.unpack("<I", version_bytes)[0]
                if version not in (1, 2, 3):
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="medium",
                            code="model_invalid_gguf_version",
                            message=f"Unknown GGUF version: {version} (expected 1, 2, or 3).",
                            details={"filename": original_filename, "version": version},
                        )
                    )

        except OSError as e:
            findings.append(
                StageFinding(
                    stage="ai_safety",
                    severity="high",
                    code="model_invalid_gguf",
                    message=f"Failed to read GGUF file: {e}",
                    details={"filename": original_filename},
                )
            )

        return findings

    def _validate_model_config(
        self, file_path: Path, original_filename: str
    ) -> list[StageFinding]:
        """Validate config.json for known model architectures."""
        findings: list[StageFinding] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return findings

        if not isinstance(data, dict):
            return findings

        architectures = data.get("architectures", [])
        model_type = data.get("model_type")

        # Check architectures
        if isinstance(architectures, list):
            for arch in architectures:
                if isinstance(arch, str) and arch not in KNOWN_ARCHITECTURES:
                    findings.append(
                        StageFinding(
                            stage="ai_safety",
                            severity="low",
                            code="model_unknown_architecture",
                            message=f"Unknown model architecture: '{arch}'.",
                            details={
                                "filename": original_filename,
                                "architecture": arch,
                            },
                        )
                    )

        # Check model_type
        if isinstance(model_type, str) and model_type not in KNOWN_MODEL_TYPES:
            findings.append(
                StageFinding(
                    stage="ai_safety",
                    severity="low",
                    code="model_unknown_architecture",
                    message=f"Unknown model type: '{model_type}'.",
                    details={
                        "filename": original_filename,
                        "model_type": model_type,
                    },
                )
            )

        return findings
