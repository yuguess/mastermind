## 启动规则

写任何代码之前, 按以下顺序完成 

1. 阅读这个文件, 这个文件定义了规则
2. 阅读feature_list.json, 来获取所有feature开发的状态, 开发完成后, 更新这个列表
3. python解释器的路径在.venv下
4. 尽量用函数式的风格来实现, 函数不要太长, 实现时尽量拆成多小的函数, 通过函数组合实现功能
5. 用Optional类型替代Any | None的类型
6. 尽量用map, filter, 多使用list comprehension方式来实现
7. for循环里尽量不要带break或者continue
8. 如果一个变量的类型可能是None或者Any, 变量名以opt_开头
9. 多使用itertools, more_itertools里的函数
10. 有副作用的函数以命名时以_SE结尾
11. 使用base_adt.py中定义的type alias替换类型名称

## tagrank

1. tagrank放程序的代码
2. test放测试文件
3. script放脚本
4. config放配置文件
5. data目录放数据
6. requirements.txt记录这个项目的依赖库
