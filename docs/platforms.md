# 平台订单接入与验证边界

> **暂不可用（2026-11-18）**：本页为保留的官方API接入资料。现有商家后台账号不等于开放平台开发者授权，程序已暂停此路径的实际调用，不能通过填写商家登录账号直接拉单。

核实日期：2026-11-17。真实调用需要平台批准应用、订单权限和对应门店授权。项目没有使用商家后台 Cookie、模拟登录或爬取商家页面。协议单元测试使用人工构造的数据，尚未进行真实商家沙箱或生产联调，不能把测试通过理解为平台权限已经开通。

## 每个门店单独配置

数据库只保存环境变量名称，实际值由部署环境或本地 `.env` 提供。环境变量没有配置时立即失败，不读取其他门店、平台或通用名称作为替代。

```json
{
  "app_key_env": "STORE_A_ELEME_APP_KEY",
  "app_secret_env": "STORE_A_ELEME_APP_SECRET",
  "access_token_env": "STORE_A_ELEME_ACCESS_TOKEN",
  "environment": "production",
  "page_size": 50,
  "max_pages": 100
}
```

`external_shop_id` 独立保存平台的门店 ID，不是开发者 ID、应用 ID 或本地 merchant_id。应用须自行完成授权和令牌更新，再向上述精确环境变量提供有效 token；当前连接器不自动执行 OAuth 或刷新 token。

## 淘宝闪购 / 饿了么餐饮

使用 [餐饮商家服务商平台](https://open.shop.ele.me/) 的餐饮订单接口。零售、用户下单接口和早期 `open.faas.ele.me` 接口不适用于本连接器。

核对依据是平台团队发布的 Java SDK `me.ele.openapi:eleme-openapi-sdk:1.31.74`，其 [Maven Central 元数据](https://central.sonatype.com/artifact/me.ele.openapi/eleme-openapi-sdk/1.31.74) 和 [完整 sources.jar](https://repo.maven.apache.org/maven2/me/ele/openapi/eleme-openapi-sdk/1.31.74/eleme-openapi-sdk-1.31.74-sources.jar) 可下载。另以 [官方 Go SDK](https://pkg.go.dev/e.coding.net/napos_openapi/eleme-openapi-go-sdk.git) 交叉核对 RPC 地址与方法。当前 Java SDK 的 `WebUtils` 使用毫秒时间戳，连接器与其保持一致。

| 用途 | 已核实的方法 / 模型 |
| --- | --- |
| 当日分页 | `eleme.order.getAllOrders(shopId,pageNo,pageSize,date)`，`OrderList.list/total` |
| 部分退款商品 | `eleme.order.queryRefundFoodInfos(orderId)`，`RefundFoodInfos.goodsList` |
| 退单成功明细 | `eleme.order.getRefundOrder(orderId)`，`ORefundOrder` |
| 订单及商品 | `OOrder`、`OGoodsGroup`、`OGoodsItem`、`OFoodItem` |
| 签名及请求 | `SignatureUtil.generateSignature`、`WebUtils.request` |

正式网关：`https://open-api.shop.ele.me/api/v1`；沙箱：`https://open-api-sandbox.shop.ele.me/api/v1`。通过 `environment=production|sandbox` 明确选择；不自动从一个环境切换到另一个环境。

请求为 JSON RPC，包含 `token/nop/metas/params/action/id/signature`。签名将业务参数与 `metas.app_key/timestamp` 合并按键排序，每项拼成 `key=紧凑JSON值`，然后计算 `MD5(action + token + 拼接值 + appSecret)` 并转大写。JSON 字符串保留双引号和中文；签名前不做 URL 编码。`error` 非空即失败。

`date` 传 `yyyy-MM-dd`，页码从 1 开始，最大 100，每页最多 50。连接器要求 `total` 与完整页数相符，遇到重复订单、总数变化、空缺页、分页上限、其他门店、非目标日期或任何接口失败，整个同步失败，不返回已取得的部分订单。单日超过平台 5000 条上限时需要另行申请可覆盖全部订单的方案。

订单时间统一转成 UTC，未带时区的平台中国时间按 `Asia/Shanghai` 处理。`totalPrice` 是用户实付，单位元，包含费用，不能直接当作商家净收入或纯商品营业额；`OGoodsItem.price` 保存原商品价，不把套餐价平分到子商品。`extra` 组中的配送费等不计为商品；正常菜品与赠品仍保留商品数量。

连接器会查询每笔订单的已确认商品退款。通过 `uniqueId` 将 `refundedUnit` 精确映射到原订单行；无法精确匹配即失败。部分退款金额来自商品 `refundedMoney` 的合计，**不包含未在该商品列表返回的包装费等退款**。退单成功时另查询 `getRefundOrder`，必须核对 `refundType`：`normal` 才将商品全部标记退款；`part` 仍按商品退款明细核对数量，并采用该退单已知金额，不能把 successful 一概当作全退。应用必须同时具备以上退款明细接口权限，否则同步不会假装成功。

### 套餐数量需要用真实样本确认

SDK 明确提供 `OGoodsItem.foodGroup` 与子商品 `OFoodItem.quantity`，但注释仅写“套餐子商品数量”，没有说明父套餐购买多份时这个数是否已乘父数量。为避免重复乘数，出现结构化套餐时必须根据获批接口文档或“购买 2 份套餐”的真实样本设置：

| 配置 | 解释 | 父套餐买 2 份、子 quantity=2 时 |
| --- | --- | --- |
| `bundle_quantity_mode=per_parent` | 子数量是一份套餐中的数量 | 每份父商品含 2 个子商品，合计 4 |
| `bundle_quantity_mode=order_total` | 子数量已是该父商品行的合计 | 每份父商品含 1 个子商品，合计 2 |

未配置时明确报错；没有结构化子商品时仍保留完整商品名称，交给应用内经确认的拆分规则处理。旧式 `combo` 分组缺少明确父子关系、多层 `subFoodGroupItem` 套餐当前也明确报错，不能猜测数量。单位和规格描述不用于自动推断重量、出成率或采购量。

## 京东秒送

[旧开放平台](https://opendj.jd.com/) 正在迁移到 [京东新开放平台](https://open.jd.com/)。迁移后文档中的方法名与实际网关域名不是一一对应，不能仅靠 `open.jd.com` 域名判断 HTTP 协议。

已确认新版文档存在 `jd.o2o.ApiPlatformService.querySellerOrderPage`（apiId 101633）：[新平台订单列表文档](https://open.jd.com/v2/#/doc/api?apiCateId=100391&apiId=101633&apiName=jd.o2o.ApiPlatformService.querySellerOrderPage&gwType=0)。文档仍列正式地址 `https://openapi.jddj.com/djapi/ApiPlatformService/querySellerOrderPage`。请求包装已按新版指南核对；公开文档的分页返回模型与字段说明存在矛盾，实际返回结构仍待真实门店联调。

`JDProvider` 已实现新版文档明确的 HTTP 请求，**平台响应合同待实店联调**。`api_mode=new`（默认）使用上述正式网关；`legacy` 明确报错，不自动切换到旧接口。`environment` 当前只接受 `production`。应用自动生成的 Java SDK 示例不是直连报文，当前秒送业务的 [SDK 说明](https://open.jd.com/v2/#/doc/dev-guide?listId=842) 明确暂不支持 SDK，不能把 `listOrderRequest` 外包或其他京东网关参数复制到此处。

当前请求依据 [新版调用指南](https://open.jd.com/v2/#/doc/dev-guide?listId=1100926)：POST 表单包含 `token/app_key/sign/timestamp/format=json/v=1.0/jd_param_json`；不使用 `api.jd.com/routerjson`、`method`、`access_token` 或 `360buy_param_json`。业务 JSON 直接包含 `deliveryStationNo/startTime/endTime/dateType=1/sortOrder=ASC/page/pageSize/returnedFields`。门店取本地 `external_shop_id`。系统注入的 `orgCode/source/appKey` 不作为业务字段发送。签名与下述到家规范一致。

该指南还要求每次请求的时间戳秒级不同，连接器在同次同步内异步等待下一秒再签名，不预造未来时间戳；系统时钟回退会明确失败。24 个小时窗口即使都是空页，也需要约 23 秒，分页较多时更长。完整同步复用一个 HTTP 客户端，调用方注入的客户端仍由调用方负责关闭。部署机器须保持时钟准确，多个进程共用同一授权时还须按平台要求协调请求速率。

按中国营业日切成 24 个不重叠的小时窗口，每个窗口从第 1 页开始。每页 1–100 条，默认 100；最多 99 页。必须同时满足外层和 `data` 内层 `code=0`，再严格读取官方字段说明提到的 `data.result.resultList` 数组；`data` 和 `result` 支持文档中的 JSON 字符串编码。短页结束当前小时，满页继续查询下一页；超页长、分页上限、未知结构、门店/日期不符、请求或业务错误都会令整个同步失败。未根据未经证实的 `totalCount` 提前结束。订单重复由应用服务按商家和平台订单号处理。

**文档矛盾仍未消除**：返回字段说明写 `data.result.resultList`，公开响应模型却描述单个 `OrderDTO`，示例也未给出真实分页数据。当前适配选择字段说明中的列表结构；若实店返回其他结构，将显式失败并要求核对，不将单个订单包装成“当日全部订单”。目前的测试只能证明人工构造的该结构正确处理，不能证明生产实际采用该结构。

金额 `orderBuyerPayableMoney`、`skuJdPrice` 按文档的分转成元；商品数量用 `skuCount`，秒送商品行以 `supplyName` 的商品 UUID 标识。缺少该 UUID、存在 `adjustIsExists` 或商品调整记录时明确失败，需要先补充已核实的调整数量合同。当前京东连接器取得的是原始下单数量，**尚未对接部分退款与售后明细，不能将该数量当作最终退款后净销量**。这部分须在权限开通后补齐售后对账，历史数据也需重跑。

已经核实并保留的旧协议依据：

- [旧版 order/es/query](https://opendj.jd.com/api/getApiDetail/169/ba3027848c3c4fda9674966e2a466482.htm)：要求按小时查询，`pageNo*pageSize<10000`，一般只支持最近一年；当前公开应用参数表却缺少按日分页与门店过滤参数，不能据历史文章自行补齐。
- [旧站 querySellerOrderPage](https://opendj.jd.com/api/getApiDetail/169/30e5d8a6a385452c94031cc673fc60cd.htm)：公开 schema 有日期、门店、`page/pageSize`，响应字段说明称 `data.result.resultList`，模型和示例却描述单个订单，需进一步确认。
- [到家侧接口开发规范](https://opendj.jd.com/staticnew/widgets/doc/newguifanchannel.html)：表单 `token/app_key/timestamp/format=json/v=1.0/jd_param_json`，timestamp 为中国日期时间字符串；除 `sign` 外按参数键排序，签名前使用未 URL 编码的值，`MD5(secret + key/value拼接 + secret)` 转大写。新版调用指南仍采用该协议。

京东 `productSubInfo` 文档明确是 JSON 数组字符串，但其中 `skuCount` 相对父商品多份购买的倍数语义仍未明确；出现结构化套餐时同样必须配置并核实 `bundle_quantity_mode`。子商品保存实际名称和 SKU，不分摊套餐金额。若响应包含 `encryptData`，当前明确失败并要求加密合同联调，即使同时含明文也不忽略密文；不能将密文响应当成无订单。

## 隐私、失败与日后对账

连接器只构造订单号、流水号、状态、时间、商品名称、SKU、数量及经营金额。不会将顾客姓名、电话、地址、备注、发票或平台完整响应交给持久层。HTTP 或平台失败消息不包含签名、token、响应正文和顾客信息。

当天轮询只能刷新目标营业日订单，不能自动发现此前日期发生的售后；需要另外重跑历史日期完成对账。真实权限、商家门店授权、token 有效性、退款权限、套餐数量口径、预订单时间口径以及平台限频行为，均须在用户获批后通过沙箱/小范围真实订单验证。
