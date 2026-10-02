"""KVM Builder and Manager parameter abstractions for kernel job submission.

Copied from Kernel_Agent.data_classes.kvm_parameters to make patch_minimizer
self-contained for kernel job execution (no kcomp/KBDr_Runner dependency at import time).

AIDEV-NOTE: These dataclasses mirror the Kernel_Agent originals exactly. Any changes
to the Kernel_Agent versions should be reflected here and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import json


@dataclass
class FtraceConfig:
    """Ftrace configuration for kernel debugging."""

    function_filter_list: List[str] = field(default_factory=list)
    buffer_size: int = 204800
    dump_on_oops: bool = True

    def to_dict(self) -> dict:
        return {
            "function-filter-list": self.function_filter_list,
            "buffer-size": self.buffer_size,
            "dump-on-oops": self.dump_on_oops,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FtraceConfig:
        return cls(
            function_filter_list=data.get("function-filter-list", []),
            buffer_size=data.get("buffer-size", 204800),
            dump_on_oops=data.get("dump-on-oops", True),
        )


@dataclass
class KdumpConfig:
    """Kdump configuration for crash dumps."""

    enabled: bool = True
    crash_kernel_size: str = "512M"

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "crash-kernel-size": self.crash_kernel_size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KdumpConfig:
        return cls(
            enabled=data.get("enabled", True),
            crash_kernel_size=data.get("crash-kernel-size", "512M"),
        )


@dataclass
class ImageConfig:
    """Pre-built kernel image configuration."""

    image_url: str
    arch: str
    vmlinux_url: str

    def to_dict(self) -> dict:
        return {
            "image-url": self.image_url,
            "arch": self.arch,
            "vmlinux-url": self.vmlinux_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ImageConfig:
        return cls(
            image_url=data.get("image-url") or data.get("image_url"),
            arch=data["arch"],
            vmlinux_url=data.get("vmlinux-url") or data.get("vmlinux_url"),
        )


@dataclass
class KBuilderParameters:
    """Kernel builder parameters for KBDr-Runner.

    AIDEV-NOTE: All fields Optional — None means "not yet populated".
    Validated by assertions before job submission.
    """

    kernel_commit_id: Optional[str] = None
    kernel_git_url: Optional[str] = None
    kernel_config: Optional[str] = None
    user_img: Optional[str] = None
    arch: Optional[str] = None
    compiler: Optional[str] = None
    linker: Optional[str] = None
    patch: Optional[str] = None
    kcache_url: Optional[str] = None

    def to_dict(self) -> dict:
        result = {}
        if self.kernel_commit_id is not None:
            result["kernel-commit-id"] = self.kernel_commit_id
        if self.kernel_git_url is not None:
            result["kernel-git-url"] = self.kernel_git_url
        if self.kernel_config is not None:
            result["kernel-config"] = self.kernel_config
        if self.user_img is not None:
            result["userspace-image-name"] = self.user_img
        if self.arch is not None:
            result["kernel-arch"] = self.arch
        if self.compiler is not None:
            result["compiler"] = self.compiler
        if self.linker is not None:
            result["linker"] = self.linker
        if self.patch is not None:
            result["patch"] = self.patch
        if self.kcache_url is not None:
            result["kcache-url"] = self.kcache_url
        return result

    @classmethod
    def from_dict(cls, data: dict) -> KBuilderParameters:
        kernel_commit_id = (
            data.get("kernel-commit-id")
            if "kernel-commit-id" in data
            else data.get("kernel_commit_id")
        )
        kernel_git_url = (
            data.get("kernel-git-url")
            if "kernel-git-url" in data
            else data.get("kernel_git_url")
        )
        if "userspace-image-name" in data:
            user_img = data["userspace-image-name"]
        elif "user-img" in data:
            user_img = data["user-img"]
        else:
            user_img = data.get("user_img")
        kernel_config = (
            data.get("kernel-config")
            if "kernel-config" in data
            else data.get("kernel_config")
        )
        kcache_url = (
            data.get("kcache-url")
            if "kcache-url" in data
            else data.get("kcache_url")
        )
        return cls(
            kernel_commit_id=kernel_commit_id,
            kernel_git_url=kernel_git_url,
            kernel_config=kernel_config,
            user_img=user_img,
            arch=(
                data.get("kernel-arch")
                if "kernel-arch" in data
                else data.get("arch")
            ),
            compiler=data.get("compiler"),
            linker=data.get("linker"),
            patch=data.get("patch"),
            kcache_url=kcache_url,
        )

    @classmethod
    def get_empty(cls) -> KBuilderParameters:
        return cls()

    def has_kcache(self) -> bool:
        return self.kcache_url is not None

    def has_patch(self) -> bool:
        return self.patch is not None and len(self.patch) > 0

    def update_kernel_config(self, config: str) -> None:
        self.kernel_config = config

    def update_patch(self, patch: str) -> None:
        self.patch = patch

    def update_compiler(self, compiler: str) -> None:
        self.compiler = compiler

    def update_linker(self, linker: str) -> None:
        self.linker = linker

    def update_kcache_url(self, kcache_url: Optional[str]) -> None:
        self.kcache_url = kcache_url


@dataclass
class KVMManagerParameters:
    """KVM manager parameters for kernel execution in VM.

    AIDEV-NOTE: image and image_from_worker are mutually exclusive:
    - image: pre-built kernel (from kernel URL)
    - image_from_worker: kernel built in current job (from kbuilder)
    """

    reproducer_type: Optional[str] = None
    reproducer_text: Optional[str] = None
    nproc: Optional[int] = None
    restart_time: Optional[str] = None
    machine_type: Optional[str] = None
    syzkaller_checkout: Optional[str] = None
    ninstance: Optional[int] = None
    threaded: Optional[bool] = None
    image_from_worker: Optional[int] = None
    image: Optional[ImageConfig] = None
    syzkaller_rollback_to_latest: Optional[bool] = None
    syzkaller_rollback_tag: Optional[str] = None
    crash_collecting_policy: Optional[str] = None
    ftrace: Optional[FtraceConfig] = None
    kdump: Optional[KdumpConfig] = None

    def to_dict(self) -> dict:
        result = {}
        reproducer = {}
        if self.reproducer_type is not None:
            reproducer["reproducer-type"] = self.reproducer_type
        if self.reproducer_text is not None:
            reproducer["reproducer-text"] = self.reproducer_text
        if self.nproc is not None:
            reproducer["nproc"] = self.nproc
        if self.restart_time is not None:
            reproducer["restart-time"] = self.restart_time
        if self.syzkaller_checkout is not None:
            reproducer["syzkaller-checkout"] = self.syzkaller_checkout
        if self.syzkaller_rollback_to_latest is not None:
            reproducer["syzkaller-rollback-to-latest"] = self.syzkaller_rollback_to_latest
        if self.syzkaller_rollback_tag is not None:
            assert self.syzkaller_rollback_tag in ("master", "kgym-latest"), (
                f"syzkaller_rollback_tag must be 'master' or 'kgym-latest', "
                f"got: {self.syzkaller_rollback_tag}"
            )
            reproducer["syzkaller-rollback-tag"] = self.syzkaller_rollback_tag
        if self.ninstance is not None:
            reproducer["ninstance"] = self.ninstance
        if self.threaded is not None:
            reproducer["threaded"] = self.threaded
        if reproducer:
            result["reproducer"] = reproducer
        if self.machine_type is not None:
            result["machine-type"] = self.machine_type
        if self.crash_collecting_policy is not None:
            result["crash-collecting-policy"] = self.crash_collecting_policy
        if self.image is not None:
            result["image"] = self.image.to_dict()
        elif self.image_from_worker is not None:
            result["image-from-worker"] = self.image_from_worker
        if self.ftrace is not None:
            result["ftrace"] = self.ftrace.to_dict()
        if self.kdump is not None:
            result["kdump"] = self.kdump.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> KVMManagerParameters:
        if "reproducer" in data:
            reproducer = data["reproducer"]
            reproducer_type = (
                reproducer.get("reproducer-type")
                if "reproducer-type" in reproducer
                else reproducer.get("reproducer_type")
            )
            reproducer_text = (
                reproducer.get("reproducer-text")
                if "reproducer-text" in reproducer
                else reproducer.get("reproducer_text")
            )
            nproc = reproducer.get("nproc")
            restart_time = (
                reproducer.get("restart-time")
                if "restart-time" in reproducer
                else reproducer.get("restart_time")
            )
            ninstance = reproducer.get("ninstance", 1)
            syzkaller_checkout = (
                reproducer.get("syzkaller-checkout")
                if "syzkaller-checkout" in reproducer
                else reproducer.get("syzkaller_checkout")
            )
            syzkaller_rollback = reproducer.get("syzkaller-rollback-to-latest", False)
            syzkaller_rollback_tag = (
                reproducer.get("syzkaller-rollback-tag")
                if "syzkaller-rollback-tag" in reproducer
                else reproducer.get("syzkaller_rollback_tag")
            )
            threaded = reproducer.get("threaded")
        else:
            reproducer_type = (
                data.get("reproducer-type")
                if "reproducer-type" in data
                else data.get("reproducer_type")
            )
            reproducer_text = (
                data.get("reproducer-text")
                if "reproducer-text" in data
                else data.get("reproducer_text")
            )
            nproc = data.get("nproc")
            restart_time = (
                data.get("restart-time")
                if "restart-time" in data
                else data.get("restart_time")
            )
            ninstance = data.get("ninstance", 1)
            syzkaller_checkout = (
                data.get("syzkaller-checkout")
                if "syzkaller-checkout" in data
                else data.get("syzkaller_checkout")
            )
            syzkaller_rollback = data.get("syzkaller-rollback-to-latest", False)
            syzkaller_rollback_tag = (
                data.get("syzkaller-rollback-tag")
                if "syzkaller-rollback-tag" in data
                else data.get("syzkaller_rollback_tag")
            )
            threaded = data.get("threaded")

        machine_type = (
            data.get("machine-type")
            if "machine-type" in data
            else data.get("machine_type")
        )
        crash_collecting_policy = (
            data.get("crash-collecting-policy")
            if "crash-collecting-policy" in data
            else data.get("crash_collecting_policy")
        )
        ftrace = None
        if "ftrace" in data and data["ftrace"] is not None:
            ftrace = FtraceConfig.from_dict(data["ftrace"])
        kdump = None
        if "kdump" in data and data["kdump"] is not None:
            kdump = KdumpConfig.from_dict(data["kdump"])
        image = None
        if "image" in data and data["image"] is not None:
            image = ImageConfig.from_dict(data["image"])
        image_from_worker_val = data.get("image-from-worker")
        if image_from_worker_val is None:
            image_from_worker_val = data.get("image_from_worker")

        return cls(
            reproducer_type=reproducer_type,
            reproducer_text=reproducer_text,
            nproc=nproc,
            restart_time=restart_time,
            machine_type=machine_type,
            syzkaller_checkout=syzkaller_checkout,
            ninstance=ninstance,
            threaded=threaded,
            image_from_worker=image_from_worker_val,
            image=image,
            syzkaller_rollback_to_latest=syzkaller_rollback,
            syzkaller_rollback_tag=syzkaller_rollback_tag,
            crash_collecting_policy=crash_collecting_policy,
            ftrace=ftrace,
            kdump=kdump,
        )

    @classmethod
    def get_empty(cls) -> KVMManagerParameters:
        return cls()

    def has_ftrace(self) -> bool:
        return self.ftrace is not None

    def has_kdump(self) -> bool:
        return self.kdump is not None

    def is_c_reproducer(self) -> bool:
        return self.reproducer_type == "c"

    def update_reproducer(self, reproducer_type: str, reproducer_text: str) -> None:
        self.reproducer_type = reproducer_type
        self.reproducer_text = reproducer_text

    def update_ftrace(self, ftrace: Optional[FtraceConfig]) -> None:
        self.ftrace = ftrace

    def update_kdump(self, kdump: Optional[KdumpConfig]) -> None:
        self.kdump = kdump

    def update_nproc(self, nproc: int) -> None:
        self.nproc = nproc


@dataclass
class CompleteKVMArguments:
    """Complete argument package for KBDr-Runner job submission."""

    bug_id: str
    kvm_builder_parameters: KBuilderParameters
    kvm_manager_parameters: KVMManagerParameters

    def to_dict(self) -> dict:
        return {
            "bug_id": self.bug_id,
            "kvm_builder_parameters": self.kvm_builder_parameters.to_dict(),
            "kvm_manager_parameters": self.kvm_manager_parameters.to_dict(),
        }

    def to_reproducer_dict(self) -> dict:
        return {
            "bug_id": self.bug_id,
            "kvm_builder_parameters": self.kvm_builder_parameters,
            "kvm_manager_parameters": self.kvm_manager_parameters,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CompleteKVMArguments:
        return cls(
            bug_id=data["bug_id"],
            kvm_builder_parameters=KBuilderParameters.from_dict(
                data["kvm_builder_parameters"]
            ),
            kvm_manager_parameters=KVMManagerParameters.from_dict(
                data["kvm_manager_parameters"]
            ),
        )

    def save_json(self, filepath: str, indent: int = 4) -> None:
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=indent)

    @classmethod
    def load_json(cls, filepath: str) -> CompleteKVMArguments:
        with open(filepath) as f:
            data = json.load(f)
        return cls.from_dict(data)

    def get_builder_parameters(self) -> KBuilderParameters:
        return self.kvm_builder_parameters

    def get_manager_parameters(self) -> KVMManagerParameters:
        return self.kvm_manager_parameters

    def set_builder_parameters(self, params: KBuilderParameters) -> None:
        self.kvm_builder_parameters = params

    def set_manager_parameters(self, params: KVMManagerParameters) -> None:
        self.kvm_manager_parameters = params
