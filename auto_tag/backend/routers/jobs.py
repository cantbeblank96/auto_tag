import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_tag.backend.job_runner import (
    delete_jobs,
    get_job,
    get_job_logs,
    get_server_started_at,
    job_log_file,
    list_jobs,
    read_failed_images,
    rerun_failed_job,
    submit_job,
)
from auto_tag.core.config import settings
from auto_tag.core.pipeline import (
    PipelineConfig,
    _read_image_list,
    build_image_filter_spec,
    normalize_work_dir,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _resolve_work_dir(work_dir: Optional[str]) -> str:
    """与 database 路由 _resolve_paths 一致：None 时从 settings.db_path 反向推导。"""
    if work_dir and str(work_dir).strip():
        return normalize_work_dir(work_dir)
    emb = os.path.realpath(
        os.path.abspath(os.path.expanduser(str(settings.db_path).strip()))
    )
    return os.path.dirname(emb.rstrip(os.sep)) or os.getcwd()


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_dirs: List[str] = Field(default_factory=list)
    image_ls_files: List[str] = Field(default_factory=list)
    work_dir: Optional[str] = Field(
        default=None,
        description="工作根目录；不传则使用服务端 config 中的 embedding_store_path 反向推导",
    )
    log_dir: Optional[str] = Field(
        default=None,
        description="已废弃；仅当 work_dir 仍为默认值且传入时，才将其当作工作根目录",
    )

    rotate_angle: Optional[str] = None
    b_yuv_image: bool = False
    mixed_yuv: bool = False
    yuv_type: str = "nv21"
    image_height: int = 0
    image_width: int = 0
    record_stage1_duplicates: Optional[bool] = None
    batch_size: Optional[int] = None
    skip_if_in_db: bool = True
    pipeline_debug: Optional[bool] = None

    image_suffixes: Optional[List[str]] = Field(
        default=None,
        description="后缀过滤（仅作用于 input_dirs 扫描）；如 [\".jpg\", \".png\"]，留空 = 不过滤",
    )
    image_name_regex: Optional[str] = Field(
        default=None,
        description="文件名正则过滤；非空时优先于 image_suffixes",
    )
    filter_ignore_case: bool = Field(default=True, description="过滤时忽略大小写")
    filter_match_full_path: bool = Field(
        default=False, description="正则匹配完整路径；False 时仅匹配文件名"
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_output_dir_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "output_dir" in data and "work_dir" not in data:
            data = {**data, "work_dir": data["output_dir"]}
        return data

    @model_validator(mode="after")
    def _legacy_log_dir(self) -> "JobCreate":
        ld = (self.log_dir or "").strip()
        if not ld:
            return self
        wd = (self.work_dir or "").strip()
        if wd in ("", "./work", "work", "./output", "output"):
            return self.model_copy(update={"work_dir": ld})
        return self


def _to_pipeline_config(body: JobCreate) -> PipelineConfig:
    wd = _resolve_work_dir(body.work_dir)
    return PipelineConfig(
        input_dirs=body.input_dirs,
        image_ls_files=body.image_ls_files,
        image_suffixes=body.image_suffixes,
        image_name_regex=body.image_name_regex,
        filter_ignore_case=body.filter_ignore_case,
        filter_match_full_path=body.filter_match_full_path,
        work_dir=wd,
        rotate_angle=body.rotate_angle,
        b_yuv_image=body.b_yuv_image,
        mixed_yuv=body.mixed_yuv,
        yuv_type=body.yuv_type,
        image_height=body.image_height,
        image_width=body.image_width,
        batch_size=body.batch_size,
        record_stage1_duplicates=body.record_stage1_duplicates,
        skip_if_in_db=body.skip_if_in_db,
        pipeline_debug=body.pipeline_debug,
    )


@router.post("")
def create_job(body: JobCreate) -> Dict[str, Any]:
    if not body.input_dirs and not body.image_ls_files:
        raise HTTPException(
            status_code=400, detail="Provide input_dirs or image_ls_files"
        )
    # 过滤条件提前校验：正则非法 / 后缀全部无效 → 400
    try:
        build_image_filter_spec(
            image_suffixes=body.image_suffixes,
            image_name_regex=body.image_name_regex,
            filter_ignore_case=body.filter_ignore_case,
            filter_match_full_path=body.filter_match_full_path,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # image_ls 文件提前校验：不存在 / 头部行非法 → 400
    for f_path in body.image_ls_files:
        if not os.path.isfile(f_path):
            raise HTTPException(
                status_code=400, detail=f"image_ls 文件不存在：{f_path}"
            )
        try:
            _read_image_list(f_path)
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"image_ls 文件 {f_path} 解析失败：{e}"
            ) from e
    cfg = _to_pipeline_config(body)
    try:
        job_id = submit_job(cfg)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"job_id": job_id}


@router.get("")
def list_all_jobs() -> Dict[str, Any]:
    """返回所有历史任务列表 + 服务启动时间。"""
    return {
        "jobs": list_jobs(),
        "server_started_at": get_server_started_at(),
    }


@router.get("/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    j = get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": j["status"],
        "processed": j["processed"],
        "total": j["total"],
        "error": j["error"],
        "failed_count": j["failed_count"],
        "failed_so_far": j.get("failed_so_far", 0),
        "skip_in_db": j.get("skip_in_db", 0),
        "vlm_calls": j.get("vlm_calls", 0),
        "vlm_failed": j.get("vlm_failed", 0),
        "new_centers": j.get("new_centers", 0),
        "stage1_skips": j.get("stage1_skips", 0),
        "stage2_joins": j.get("stage2_joins", 0),
        "created_at": j.get("created_at", 0),
        "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
    }


@router.get("/{job_id}/logs")
def job_logs(job_id: str, tail: int = 200) -> Dict[str, Any]:
    j = get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    lines = get_job_logs(job_id, tail=tail)
    return {"job_id": job_id, "lines": lines}


@router.get("/{job_id}/logs/download")
def job_logs_download(job_id: str) -> FileResponse:
    """下载任务完整日志文件（work_dir/log/jobs/job_{job_id}.log）。"""
    j = get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    path = job_log_file(job_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="该任务日志文件不存在（日志落盘于 v0.0.5 后启用，历史任务可能无日志）",
        )
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=f"job_{job_id[:8]}.log",
    )


@router.post("/{job_id}/rerun_failed")
def job_rerun_failed(job_id: str) -> Dict[str, Any]:
    """单独重跑该任务的失败图片（新建一个仅含失败列表的任务）。"""
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        new_job_id = rerun_failed_job(job_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"job_id": new_job_id, "source_job_id": job_id,
            "failed_count": len(read_failed_images(job_id))}


class JobsDeleteRequest(BaseModel):
    job_ids: List[str] = Field(default_factory=list)


@router.delete("")
def delete_job_records_api(body: JobsDeleteRequest) -> Dict[str, Any]:
    """批量删除任务记录（仅删后端记录，不删 work_dir 产物）；运行中任务拒绝删除。"""
    if not body.job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")
    return delete_jobs(body.job_ids)
