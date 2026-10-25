/**
 * 中国城市列表 — 供天气监控城市搜索下拉（本地搜索，无需运行时请求）。
 * 覆盖直辖市 + 省会 + 主要地级市。城市名用于 wttr.in 天气查询。
 * 开源者可按需增删。
 */
export interface CityEntry { name: string; province: string }

export const CHINA_CITIES: CityEntry[] = [
  // 直辖市
  { name: '北京', province: '北京' },
  { name: '上海', province: '上海' },
  { name: '天津', province: '天津' },
  { name: '重庆', province: '重庆' },
  // 河北
  { name: '石家庄', province: '河北' }, { name: '唐山', province: '河北' },
  { name: '保定', province: '河北' }, { name: '廊坊', province: '河北' },
  { name: '秦皇岛', province: '河北' }, { name: '邯郸', province: '河北' },
  { name: '张家口', province: '河北' }, { name: '沧州', province: '河北' },
  // 山西
  { name: '太原', province: '山西' }, { name: '大同', province: '山西' },
  { name: '临汾', province: '山西' }, { name: '运城', province: '山西' },
  // 内蒙古
  { name: '呼和浩特', province: '内蒙古' }, { name: '包头', province: '内蒙古' },
  { name: '鄂尔多斯', province: '内蒙古' }, { name: '赤峰', province: '内蒙古' },
  // 辽宁
  { name: '沈阳', province: '辽宁' }, { name: '大连', province: '辽宁' },
  { name: '鞍山', province: '辽宁' }, { name: '锦州', province: '辽宁' },
  // 吉林
  { name: '长春', province: '吉林' }, { name: '吉林', province: '吉林' },
  { name: '延吉', province: '吉林' },
  // 黑龙江
  { name: '哈尔滨', province: '黑龙江' }, { name: '大庆', province: '黑龙江' },
  { name: '齐齐哈尔', province: '黑龙江' }, { name: '牡丹江', province: '黑龙江' },
  // 江苏
  { name: '南京', province: '江苏' }, { name: '苏州', province: '江苏' },
  { name: '无锡', province: '江苏' }, { name: '常州', province: '江苏' },
  { name: '徐州', province: '江苏' }, { name: '南通', province: '江苏' },
  { name: '扬州', province: '江苏' }, { name: '盐城', province: '江苏' },
  // 浙江
  { name: '杭州', province: '浙江' }, { name: '宁波', province: '浙江' },
  { name: '温州', province: '浙江' }, { name: '绍兴', province: '浙江' },
  { name: '嘉兴', province: '浙江' }, { name: '金华', province: '浙江' },
  { name: '台州', province: '浙江' }, { name: '湖州', province: '浙江' },
  // 安徽
  { name: '合肥', province: '安徽' }, { name: '芜湖', province: '安徽' },
  { name: '蚌埠', province: '安徽' }, { name: '安庆', province: '安徽' },
  // 福建
  { name: '福州', province: '福建' }, { name: '厦门', province: '福建' },
  { name: '泉州', province: '福建' }, { name: '漳州', province: '福建' },
  // 江西
  { name: '南昌', province: '江西' }, { name: '赣州', province: '江西' },
  { name: '九江', province: '江西' }, { name: '上饶', province: '江西' },
  // 山东
  { name: '济南', province: '山东' }, { name: '青岛', province: '山东' },
  { name: '烟台', province: '山东' }, { name: '潍坊', province: '山东' },
  { name: '临沂', province: '山东' }, { name: '淄博', province: '山东' },
  { name: '济宁', province: '山东' }, { name: '威海', province: '山东' },
  // 河南
  { name: '郑州', province: '河南' }, { name: '洛阳', province: '河南' },
  { name: '开封', province: '河南' }, { name: '南阳', province: '河南' },
  { name: '新乡', province: '河南' }, { name: '许昌', province: '河南' },
  // 湖北
  { name: '武汉', province: '湖北' }, { name: '宜昌', province: '湖北' },
  { name: '襄阳', province: '湖北' }, { name: '荆州', province: '湖北' },
  // 湖南
  { name: '长沙', province: '湖南' }, { name: '株洲', province: '湖南' },
  { name: '湘潭', province: '湖南' }, { name: '衡阳', province: '湖南' },
  { name: '岳阳', province: '湖南' }, { name: '常德', province: '湖南' },
  // 广东
  { name: '广州', province: '广东' }, { name: '深圳', province: '广东' },
  { name: '珠海', province: '广东' }, { name: '佛山', province: '广东' },
  { name: '东莞', province: '广东' }, { name: '中山', province: '广东' },
  { name: '惠州', province: '广东' }, { name: '汕头', province: '广东' },
  { name: '江门', province: '广东' }, { name: '湛江', province: '广东' },
  // 广西
  { name: '南宁', province: '广西' }, { name: '桂林', province: '广西' },
  { name: '柳州', province: '广西' }, { name: '北海', province: '广西' },
  // 海南
  { name: '海口', province: '海南' }, { name: '三亚', province: '海南' },
  // 四川
  { name: '成都', province: '四川' }, { name: '绵阳', province: '四川' },
  { name: '德阳', province: '四川' }, { name: '南充', province: '四川' },
  { name: '宜宾', province: '四川' }, { name: '泸州', province: '四川' },
  { name: '乐山', province: '四川' }, { name: '自贡', province: '四川' },
  // 贵州
  { name: '贵阳', province: '贵州' }, { name: '遵义', province: '贵州' },
  { name: '六盘水', province: '贵州' },
  // 云南
  { name: '昆明', province: '云南' }, { name: '大理', province: '云南' },
  { name: '丽江', province: '云南' }, { name: '曲靖', province: '云南' },
  // 西藏
  { name: '拉萨', province: '西藏' }, { name: '日喀则', province: '西藏' },
  // 陕西
  { name: '西安', province: '陕西' }, { name: '宝鸡', province: '陕西' },
  { name: '咸阳', province: '陕西' }, { name: '渭南', province: '陕西' },
  // 甘肃
  { name: '兰州', province: '甘肃' }, { name: '天水', province: '甘肃' },
  { name: '酒泉', province: '甘肃' },
  // 青海
  { name: '西宁', province: '青海' },
  // 宁夏
  { name: '银川', province: '宁夏' }, { name: '石嘴山', province: '宁夏' },
  // 新疆
  { name: '乌鲁木齐', province: '新疆' }, { name: '喀什', province: '新疆' },
  { name: '克拉玛依', province: '新疆' }, { name: '伊犁', province: '新疆' },
  // 港澳台
  { name: '香港', province: '香港' }, { name: '澳门', province: '澳门' },
  { name: '台北', province: '台湾' }, { name: '高雄', province: '台湾' },
];

/** 本地模糊搜索：按城市名或省份匹配，返回前 limit 条 */
export function searchCities(query: string, limit = 20): CityEntry[] {
  const q = query.trim();
  if (!q) return CHINA_CITIES.slice(0, limit);
  return CHINA_CITIES.filter(c => c.name.includes(q) || c.province.includes(q)).slice(0, limit);
}
