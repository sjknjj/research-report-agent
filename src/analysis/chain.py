CHAIN_DB = {
    "锂电池": {
        "upstream": [
            {"name": "赣锋锂业", "code": "002460", "role": "锂盐龙头，碳酸锂/氢氧化锂"},
            {"name": "天齐锂业", "code": "002466", "role": "锂精矿+锂盐，控股泰利森"},
            {"name": "华友钴业", "code": "603799", "role": "钴镍资源+三元前驱体"},
            {"name": "天赐材料", "code": "002709", "role": "电解液龙头"},
        ],
        "midstream": [
            {"name": "宁德时代", "code": "300750", "role": "动力电池+储能电池全球龙头"},
            {"name": "比亚迪", "code": "002594", "role": "电池+整车垂直一体化"},
            {"name": "亿纬锂能", "code": "300014", "role": "动力电池+储能电池"},
            {"name": "国轩高科", "code": "002074", "role": "铁锂电池"},
        ],
        "downstream": [
            {"name": "特斯拉", "code": "TSLA", "role": "新能源车全球龙头"},
            {"name": "上汽集团", "code": "600104", "role": "传统车企转型新能源"},
            {"name": "储能集成商", "code": "", "role": "风光储电站、户储"},
        ],
    },
    "新能源车": {
        "upstream": [
            {"name": "宁德时代", "code": "300750", "role": "动力电池"},
            {"name": "拓普集团", "code": "601689", "role": "汽车零部件/底盘"},
            {"name": "伯特利", "code": "603596", "role": "制动系统"},
        ],
        "midstream": [
            {"name": "比亚迪", "code": "002594", "role": "整车+电池一体化龙头"},
            {"name": "赛力斯", "code": "601127", "role": "华为问界系列整车"},
            {"name": "长安汽车", "code": "000625", "role": "传统车企深蓝/阿维塔"},
        ],
        "downstream": [
            {"name": "充电桩运营商", "code": "", "role": "特来电/星星充电等"},
            {"name": "出行平台", "code": "", "role": "滴滴/网约车/租赁"},
        ],
    },
    "白酒": {
        "upstream": [
            {"name": "贵州茅台", "code": "600519", "role": "高端白酒龙头，酱香"},
            {"name": "五粮液", "code": "000858", "role": "高端白酒，浓香"},
        ],
        "midstream": [
            {"name": "泸州老窖", "code": "000568", "role": "高端国窖1573"},
            {"name": "山西汾酒", "code": "600809", "role": "清香型龙头"},
            {"name": "洋河股份", "code": "002304", "role": "次高端，梦之蓝"},
        ],
        "downstream": [
            {"name": "经销商/渠道", "code": "", "role": "批发零售、电商平台、烟酒行"},
        ],
    },
    "半导体": {
        "upstream": [
            {"name": "中芯国际", "code": "688981", "role": "晶圆制造代工龙头"},
            {"name": "北方华创", "code": "002371", "role": "半导体设备（刻蚀/薄膜）"},
            {"name": "沪硅产业", "code": "688126", "role": "大硅片材料"},
        ],
        "midstream": [
            {"name": "韦尔股份", "code": "603501", "role": "CMOS图像传感器设计"},
            {"name": "兆易创新", "code": "603986", "role": "存储芯片+MCU设计"},
            {"name": "海光信息", "code": "688041", "role": "国产CPU/DCU"},
        ],
        "downstream": [
            {"name": "立讯精密", "code": "002475", "role": "消费电子组装"},
            {"name": "工业富联", "code": "601138", "role": "通信设备+服务器组装"},
            {"name": "紫光股份", "code": "000938", "role": "网络设备"},
        ],
    },
    "光伏": {
        "upstream": [
            {"name": "通威股份", "code": "600438", "role": "硅料+电池片"},
            {"name": "隆基绿能", "code": "601012", "role": "硅片+组件龙头"},
            {"name": "福斯特", "code": "603806", "role": "光伏胶膜"},
        ],
        "midstream": [
            {"name": "阳光电源", "code": "300274", "role": "逆变器龙头+储能"},
            {"name": "晶澳科技", "code": "002459", "role": "组件一体化"},
            {"name": "天合光能", "code": "688599", "role": "组件+储能"},
        ],
        "downstream": [
            {"name": "三峡能源", "code": "600905", "role": "光伏电站运营"},
            {"name": "国家能源集团", "code": "", "role": "大型电站央企"},
        ],
    },
    "风电": {
        "upstream": [
            {"name": "金风科技", "code": "002202", "role": "风机整机龙头"},
            {"name": "明阳智能", "code": "601615", "role": "海上风机"},
        ],
        "midstream": [
            {"name": "日月股份", "code": "603218", "role": "铸件/锻件"},
            {"name": "大金重工", "code": "002487", "role": "塔筒/桩基"},
        ],
        "downstream": [
            {"name": "龙源电力", "code": "001289", "role": "风电运营商"},
        ],
    },
    "医药": {
        "upstream": [
            {"name": "药明康德", "code": "603259", "role": "CXO龙头，研发外包"},
            {"name": "恒瑞医药", "code": "600276", "role": "创新药龙头"},
        ],
        "midstream": [
            {"name": "迈瑞医疗", "code": "300760", "role": "医疗器械龙头"},
            {"name": "云南白药", "code": "000538", "role": "中药品牌"},
            {"name": "智飞生物", "code": "300122", "role": "疫苗代理+自研"},
        ],
        "downstream": [
            {"name": "医院/药店", "code": "", "role": "公立医院、连锁药房"},
        ],
    },
    "军工": {
        "upstream": [
            {"name": "中航沈飞", "code": "600760", "role": "战斗机整机"},
            {"name": "中航西飞", "code": "000768", "role": "运输机/轰炸机"},
        ],
        "midstream": [
            {"name": "中航光电", "code": "002179", "role": "军用连接器"},
            {"name": "航发动力", "code": "600893", "role": "航空发动机"},
        ],
        "downstream": [
            {"name": "军方采购", "code": "", "role": "列装采购、军贸出口"},
        ],
    },
    "银行": {
        "upstream": [
            {"name": "工商银行", "code": "601398", "role": "国有大行龙头"},
            {"name": "招商银行", "code": "600036", "role": "股份行零售之王"},
        ],
        "midstream": [
            {"name": "宁波银行", "code": "002142", "role": "城商行标杆"},
            {"name": "成都银行", "code": "601838", "role": "西部城商行"},
        ],
        "downstream": [
            {"name": "企业/居民贷款", "code": "", "role": "对公贷款、按揭、消费贷"},
        ],
    },
    "券商": {
        "upstream": [
            {"name": "中信证券", "code": "600030", "role": "券商龙头，全业务链"},
            {"name": "东方财富", "code": "300059", "role": "互联网券商+基金销售"},
        ],
        "midstream": [
            {"name": "华泰证券", "code": "601688", "role": "机构服务+财富管理"},
        ],
        "downstream": [
            {"name": "投资者/上市公司", "code": "", "role": "经纪、投行、资管"},
        ],
    },
    "消费电子": {
        "upstream": [
            {"name": "歌尔股份", "code": "002241", "role": "声学器件/VR代工"},
            {"name": "立讯精密", "code": "002475", "role": "连接器+整机组装"},
        ],
        "midstream": [
            {"name": "蓝思科技", "code": "300433", "role": "玻璃盖板"},
            {"name": "舜宇光学", "code": "2382.HK", "role": "镜头模组"},
        ],
        "downstream": [
            {"name": "苹果", "code": "AAPL", "role": "终端品牌商"},
            {"name": "华为", "code": "", "role": "手机/PC/穿戴"},
        ],
    },
    "食品饮料": {
        "upstream": [
            {"name": "伊利股份", "code": "600887", "role": "乳制品龙头"},
            {"name": "海天味业", "code": "603288", "role": "调味品龙头"},
        ],
        "midstream": [
            {"name": "双汇发展", "code": "000895", "role": "肉制品"},
            {"name": "东鹏饮料", "code": "605499", "role": "功能饮料"},
        ],
        "downstream": [
            {"name": "商超/电商", "code": "", "role": "超市、便利店、直播电商"},
        ],
    },
    "房地产": {
        "upstream": [
            {"name": "保利发展", "code": "600048", "role": "央企地产龙头"},
            {"name": "万科A", "code": "000002", "role": "地产龙头"},
        ],
        "midstream": [
            {"name": "海螺水泥", "code": "600585", "role": "水泥建材"},
            {"name": "东方雨虹", "code": "002271", "role": "防水建材"},
        ],
        "downstream": [
            {"name": "购房者", "code": "", "role": "住宅/商业地产终端"},
        ],
    },
    "煤炭钢铁": {
        "upstream": [
            {"name": "中国神华", "code": "601088", "role": "煤炭+电力一体化龙头"},
            {"name": "陕西煤业", "code": "601225", "role": "优质动力煤"},
        ],
        "midstream": [
            {"name": "宝钢股份", "code": "600019", "role": "钢铁龙头"},
            {"name": "中信特钢", "code": "000708", "role": "特钢"},
        ],
        "downstream": [
            {"name": "基建/制造业", "code": "", "role": "建筑、机械、汽车用钢"},
        ],
    },
}


def find_chain(symbol: str, name: str = "") -> dict | None:
    code = str(symbol).strip()
    for chain_name, layers in CHAIN_DB.items():
        for layer in ("upstream", "midstream", "downstream"):
            for comp in layers[layer]:
                if comp["code"] == code or (name and comp["name"] == name):
                    return {"chain": chain_name, "position": layer, **layers}
    return None


def chain_to_text(chain_info: dict) -> str:
    if not chain_info:
        return ""
    lines = [f"【{chain_info['chain']}产业链】（本公司位于{_pos_cn(chain_info['position'])}）"]
    for layer, label in [("upstream", "上游·原材料/核心部件"), ("midstream", "中游·制造/品牌"), ("downstream", "下游·应用/渠道")]:
        lines.append(f"\n{label}：")
        for comp in chain_info.get(layer, []):
            code_str = f"（{comp['code']}）" if comp["code"] else ""
            lines.append(f"  - {comp['name']}{code_str}：{comp['role']}")
    return "\n".join(lines)


def list_chains() -> list:
    return list(CHAIN_DB.keys())


def _pos_cn(pos: str) -> str:
    return {"upstream": "上游", "midstream": "中游", "downstream": "下游"}.get(pos, pos)
