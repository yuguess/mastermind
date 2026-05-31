## 启动规则

写任何代码之前, 按以下顺序完成 

- 阅读这个文件, 这个文件定义了规则
- 阅读feature_list.json, 来获取所有feature开发的状态, 开发完成后, 更新这个列表 
- python解释器的路径在.venv下 
- 代码结构
  - 可读性第一, 在不违背可读性原则基础上, 多用列表生成式, map, reduce, filter等高阶函数来替代for循环
    - list(map(lambda x,...))的形式直接用列表推导式替代 
  - 尽量在上层调用有副作用的函数, 保持调用栈深层的函数无副作用, 如果深层调用栈函数需要调用有副作用函数, 则将dataclass类型的数据返回给浅层调用栈的函数, 即在浅层,上层进行有副作用函数的调用
    - Effects pushed to the outer layers of the program (hexagonal architecture), Within reasonable bounds, functions in the guts of programs are pure and return data, whereas the outer layers interpret that data and manage effects (IO, non-determinism, etc)
  - Programs structured mainly around data and functions, Programs are trees of function invocations on data, rather than class hierarchies, dependency injection, and overuse of exceptions
    - 多用dataclass描述数据
  - 多用函数式的风格来实现, 函数不要太长, 实现时尽量拆成多小的函数, 通过函数组合实现功能, 每个函数的功能尽量单一
  - for循环里尽量不要带break, continue或者return
- 代码类型约束
  - if和else分支中表达式或者语句尽量返回相同的类型
  - 用Optional类型替代Any | None的类型 
  - 如果一个变量的类型可能是None或者Any, 变量名以opt_开头 
  - 多使用itertools, more_itertools里的函数
- 代码可读性
  - 有副作用的函数以命名时以_SE结尾 
  - 使用base_adt.py中定义的type alias替换类型名称  
- 所有带网络请求的函数都要处理网络慢或者网络不存在的情况, 并加上重试的逻辑

## 代码目录结构

- test放测试文件 
- script放脚本 
- config放配置文件 
- data目录放数据 
- draft目录放示例, 执行一次的脚本代码 
- doc, 放文档 
- plan, 放规划文件 
- requirements.txt记录这个项目的依赖库
