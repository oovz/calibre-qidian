## calibre-qidian
Calibre 起点书籍信息和封面下载插件
部分代码基于 [calibre-douban](https://github.com/fugary/calibre-douban)

### 安装方法

下载地址：https://github.com/oovz/calibre-qidian/releases

* release页面下载zip包
* calibre中选择首选项 > 插件 > 从文件加载插件

### 使用方法

* 你的书籍必须有标题，作者或标识符```qidian: qidian_id```中的一种
* 在calibre中选择一个或多个书籍 > 右键 > 下载元数据和封面
* 如果你使用标题/作者进行搜索，请再次运行 下载元数据和封面 以便获取作者自定义封面，标签，出版日期等信息

### 测试

* 运行```calibre-customize -b <项目目录>```将插件推送到calibre测试模式
* 运行```calibre-debug -e "<项目目录>\src\__init__.py"```进行测试