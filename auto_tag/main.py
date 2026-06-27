import argparse
import logging
import os
import sys

def _apply_config_file_env_early() -> None:
    """在导入 auto_tag.core（会读 config）之前解析 --config_file。"""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--config_file", default=None, help=argparse.SUPPRESS)
    args, _ = p.parse_known_args(sys.argv[1:])
    if args.config_file:
        os.environ["AUTO_TAG_CONFIG_FILE"] = os.path.abspath(
            os.path.expanduser(args.config_file)
        )


_apply_config_file_env_early()

from auto_tag.core.pipeline import (  # noqa: E402
    PipelineConfig,
    collect_image_paths,
    work_log_dir,
    run_annotation_pipeline,
    save_verify_samples,
)


def setup_logging(log_dir: str):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, "auto_tag.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Image Auto Annotator System")
    parser.add_argument("--input_dir", action="append", default=[], help="Input directories.")
    parser.add_argument("--image_ls_file", action="append", default=[], help="JSON files containing image list.")
    parser.add_argument(
        "--work_dir",
        default=None,
        help="工作根目录：日志写入 work_dir/log，向量索引写入 work_dir/<embedding_subdir>（默认可为 embedding_index，旧数据可为 chroma_data）。",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        help="已废弃，请使用 --work_dir。",
    )
    parser.add_argument(
        "--config_file",
        default=None,
        help="JSON 配置文件路径（结构同 auto_tag/config.json）；须在首次导入业务模块前生效，已通过环境变量应用。",
    )
    parser.add_argument("--rotate_angle", help="Rotation angle (e.g. ROTATE_90_CLOCKWISE).")
    parser.add_argument("--b_yuv_image", action="store_true", help="Whether inputs are YUV images.")
    parser.add_argument(
        "--b_mixed_yuv",
        action="store_true",
        help="Mixed directory: .nv21/.nv12/.yuv as raw YUV, others as normal images.",
    )
    parser.add_argument("--yuv_type", default="nv21", help="YUV type (nv21, nv12, yuv420p).")
    parser.add_argument("--image_height", type=int, default=0, help="Height for YUV images.")
    parser.add_argument("--image_width", type=int, default=0, help="Width for YUV images.")
    parser.add_argument("--b_skip_image_manually_verified", action="store_true", help="Skip manual verification.")

    args = parser.parse_args()
    # --config_file 已在模块顶部通过 parse_known_args 写入 AUTO_TAG_CONFIG_FILE

    work_d = args.work_dir or args.output_dir or "./work"
    log_d = work_log_dir(work_d)
    logger = setup_logging(log_d)

    cfg = PipelineConfig(
        input_dirs=args.input_dir,
        image_ls_files=args.image_ls_file,
        work_dir=work_d,
        rotate_angle=args.rotate_angle,
        b_yuv_image=args.b_yuv_image,
        mixed_yuv=args.b_mixed_yuv,
        yuv_type=args.yuv_type,
        image_height=args.image_height,
        image_width=args.image_width,
    )

    image_list, all_sources = collect_image_paths(cfg.input_dirs, cfg.image_ls_files)
    if not image_list:
        logger.warning("No images found to process.")
        return

    if not args.b_skip_image_manually_verified:
        logger.info("Starting manual verification phase...")
        save_verify_samples(all_sources, log_d, cfg)
        user_input = input("Please check the sample images in work_dir/log. Continue? [Y/n]: ")
        if user_input.lower() not in ["y", "yes", ""]:
            logger.info("Processing cancelled by user.")
            return

    run_annotation_pipeline(cfg)
    logger.info("All processing finished.")


if __name__ == "__main__":
    main()
