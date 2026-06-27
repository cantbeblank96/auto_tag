你现在是一位资深的 AI 算法架构师和 Python 后端开发专家。请仔细阅读我提供的 /home/SENSETIME/xukaiming/Desktop/my_repos/python_projects/agent_for_data/auto_tag/plan.md 《自动图像标注系统工程实施方案》（基于 ChromaDB + CLIP + VLM），并以此为蓝图，从零开始在 /home/SENSETIME/xukaiming/Desktop/my_repos/python_projects/agent_for_data/auto_tag 下面实现这个工程项目。

我需要你产出具备生产环境雏形、模块化、带有完整类型提示（Type Hints）和日志记录（Logging）的 Python 代码。

【核心任务与技术约束】
请务必严格遵守以下技术规范进行开发：

1. 模块化架构：
   不要把所有代码塞在一个文件里。请按职责拆分模块，例如：
   - `config.py`：集中管理配置（如模型路径、ChromaDB 路径、距离阈值 $ \tau_{dup} $ 和 $ \tau_{cls} $ 等）。
   - `feature_extractor.py`：封装 CLIP/SigLIP 模型。必须实现批处理（Batch Processing）特征提取逻辑，而不是单张处理，以提升 GPU 利用率。
   - `vector_db.py`：封装 ChromaDB 的操作（初始化、批量查询、批量插入），必须使用 persistent client 和 cosine 距离。
   - `vlm_client.py`：封装大语言模型/多模态模型 API 调用。必须实现指数退避重试（Exponential Backoff）以及强制要求输出 JSON Schema 的逻辑。
   - `main.py` 或 `annotator.py`：实现双阈值增量聚类的核心业务流，整合上述组件。

2. 双阈值聚类逻辑的精确性：
   - 阶段 1：距离 $ d \le \tau_{dup} $，记录冗余并继承标签，不入库。
   - 阶段 2：距离 $ \tau_{dup} < d \le \tau_{cls} $，入库并继承 Cluster ID 与标签，不触发 VLM。
   - 阶段 3：距离 $ d > \tau_{cls} $，作为新簇中心入库，并触发 VLM API 获取结构化标签。

3. 工程化标准：
   - 使用标准库 `logging` 替代原有的 `print`。
   - 添加异常捕获机制，确保处理单张图片/单批次图片失败时，不会导致整个批处理任务崩溃。
   - 对 VLM 接口的返回内容进行 JSON 校验和解析。

【交付物要求】
请按照以下顺序输出你的结果：
1. 项目的目录结构树（Directory Tree）。
2. `requirements.txt`：列出所有需要的第三方依赖及推荐版本。
3. 各个模块的完整 Python 源码，并在关键逻辑处（尤其是特征提取和双阈值判断）补充中文注释。

【其他要求】

1. 我给你足够的权限，开发工程中尽量自己完成，如无必要不必打扰我。



