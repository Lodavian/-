# “极速下单”Liquid Glass 动效方案

## 1. 方案概述

这一版采用“轻量 CSS Liquid Glass 交互层”：不追求真实光学折射，而是通过透明材质、边缘高光、动态反射、悬浮阴影、缩放位移和按压回弹，共同营造类似 iOS 26 的液态玻璃反馈。

项目中的完整实现位于 `static/styles.css`。

设计遵循以下原则：

- 玻璃效果主要用于按钮、导航和操作控件，不铺满内容区域。
- 静止状态保持克制，用户交互时玻璃才明显“活起来”。
- 动效只使用 CSS，不需要 JavaScript、Canvas、图片或动画框架。
- 通过 `transform` 改变视觉尺寸，不改变元素真实宽高，因此不会引发布局重排。

最终效果由五部分组成：

1. 半透明玻璃底层。
2. 按钮上方的柔和环境高光。
3. 横向移动的流动反射。
4. 内外边缘描边和阴影。
5. 悬停浮起、按下回弹的物理反馈。

## 2. 全局设计变量

常用视觉参数被提取为 CSS 变量：

```css
:root {
  --glass-edge: rgba(202, 226, 255, 0.38);
  --glass-glow: rgba(94, 165, 255, 0.24);
  --glass-ease: cubic-bezier(.2, .82, .2, 1);
}
```

| 变量 | 用途 |
|---|---|
| `--glass-edge` | 悬停时按钮边缘的冰蓝色玻璃描边 |
| `--glass-glow` | 按钮浮起后向外扩散的蓝色环境光 |
| `--glass-ease` | 较柔软、前快后慢的液态运动曲线 |

迁移到其他品牌时，通常只需修改 `--glass-edge` 和 `--glass-glow`，整个动效便能适配新的主题色。

例如绿色主题：

```css
:root {
  --glass-edge: rgba(189, 255, 226, 0.42);
  --glass-glow: rgba(74, 222, 158, 0.25);
}
```

## 3. 基础玻璃材质

所有按钮首先获得统一的玻璃基础：

```css
button {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  transform: translate3d(0, 0, 0);
  transform-origin: center;
  backdrop-filter: blur(16px) saturate(145%);
  -webkit-backdrop-filter: blur(16px) saturate(145%);
  -webkit-tap-highlight-color: transparent;
}
```

各属性作用：

- `position: relative`：为高光伪元素建立定位环境。
- `isolation: isolate`：建立独立层叠上下文，避免高光和阴影影响其他元素。
- `overflow: hidden`：将流动高光裁切在圆角按钮内部。
- `translate3d(0, 0, 0)`：让浏览器更容易将变换交给合成层处理。
- `transform-origin: center`：保证放大从按钮中心展开。
- `backdrop-filter`：提供磨砂和色彩饱和效果。
- `-webkit-backdrop-filter`：兼容 Safari。
- `tap-highlight-color`：去除移动端浏览器默认点击闪光。

这部分不负责按钮的业务颜色。买入、卖出和主要操作按钮仍可保留自己的背景色及渐变。

## 4. 双层高光结构

玻璃质感主要由 `::before` 和 `::after` 两个伪元素构成。

### 4.1 `::before`：动态反射层

```css
button::before {
  content: "";
  position: absolute;
  z-index: 0;
  inset: 1px;
  pointer-events: none;
  border-radius: inherit;
  background:
    radial-gradient(
      140% 95% at 18% -12%,
      rgba(255,255,255,.34),
      transparent 42%
    ),
    linear-gradient(
      118deg,
      transparent 24%,
      rgba(255,255,255,.13) 45%,
      transparent 66%
    );
  opacity: .38;
  transform: translate3d(-10%, 0, 0);
  transition:
    opacity .24s var(--glass-ease),
    transform .48s var(--glass-ease);
}
```

这里叠加了两种光：

- `radial-gradient` 模拟玻璃上缘接受环境光形成的柔和亮斑。
- `linear-gradient` 模拟狭长的反射光带。

默认情况下光带位于按钮偏左位置；悬停时向右滑动，形成液体表面随交互改变反射角度的感觉。

### 4.2 `::after`：玻璃边缘层

```css
button::after {
  content: "";
  position: absolute;
  z-index: 2;
  inset: 0;
  pointer-events: none;
  border-radius: inherit;
  border: 1px solid rgba(255,255,255,.06);
  box-shadow:
    inset 0 1px rgba(255,255,255,.15),
    inset 0 -1px rgba(1,8,18,.22);
  opacity: .7;
}
```

这个层负责：

- 顶部亮边；
- 底部暗边；
- 玻璃厚度感；
- 保持不同颜色按钮之间的材质一致性。

`pointer-events: none` 很重要，否则伪元素可能拦截按钮点击。

## 5. 交互状态设计

| 状态 | 位移与缩放 | 亮度 | 视觉含义 |
|---|---:|---:|---|
| 默认 | `scale(1)` | 正常 | 安静的玻璃控件 |
| 悬停 | `translateY(-4px) scale(1.045)` | `1.1` | 浮出内容层，准备操作 |
| 键盘聚焦 | 不强制放大 | 增加焦点光环 | 表明键盘当前操作对象 |
| 按下 | `scale(.975)` | `0.98` | 模拟手指压缩玻璃 |
| 禁用 | 不动，高光降至 `.12` | 降低 | 明确不可操作 |

### 5.1 悬停浮起

```css
button:not(:disabled):not([aria-disabled="true"]):hover {
  z-index: 4;
  transform: translateY(-4px) scale(1.045);
  border-color: var(--glass-edge);
  box-shadow:
    0 18px 38px rgba(1,7,18,.46),
    0 8px 28px var(--glass-glow),
    inset 0 1px rgba(255,255,255,.25),
    inset 0 -1px rgba(7,17,33,.18);
  filter: brightness(1.1) saturate(1.14);
}
```

阴影被分为四层：

1. 深色大阴影制造控件离开页面的高度。
2. 主题色光晕营造发光玻璃效果。
3. 顶部内高光表现玻璃上缘。
4. 底部内暗边表现材质厚度。

同时流动高光从 `-10%` 移动到 `12%`：

```css
button:hover::before {
  opacity: .92;
  transform: translate3d(12%, 0, 0);
}
```

因此用户看到的不只是按钮变亮，还会看到光线沿玻璃表面移动。

### 5.2 按压回弹

```css
button:not(:disabled):not([aria-disabled="true"]):active {
  transform: translateY(0) scale(.975);
  filter: brightness(.98) saturate(1.06);
  transition-duration: .09s;
}
```

按压速度只有 `90ms`，比悬停的 `240ms` 更快，使点击反馈显得直接。松开后使用原来的液态缓动曲线恢复，形成自然回弹。

### 5.3 键盘聚焦

```css
button:not(:disabled):not([aria-disabled="true"]):focus-visible {
  z-index: 4;
  outline: none;
  border-color: rgba(158,204,255,.7);
  box-shadow:
    0 0 0 3px rgba(110,168,255,.17),
    0 15px 34px rgba(1,7,18,.42),
    0 7px 24px var(--glass-glow),
    inset 0 1px rgba(255,255,255,.22);
}
```

没有简单删除浏览器焦点框，而是用更加明显的玻璃光环替代，确保键盘操作仍然可见。

## 6. 不同控件的动效分级

并非所有按钮都使用相同比例。

| 控件类型 | 悬停效果 | 原因 |
|---|---|---|
| 普通按钮 | `translateY(-4px) scale(1.045)` | 标准反馈 |
| 图标按钮 | `translateY(-3px) scale(1.08)` | 图标面积小，需要更明显 |
| 买入/卖出按钮 | `translateY(-4px) scale(1.028)` | 按钮较大，避免遮挡和压迫 |
| 股票搜索结果 | `translateY(-2px) scale(1.015)` | 列表密集，避免相邻结果互相覆盖 |

对应代码：

```css
.trade-button:not(:disabled):hover {
  transform: translateY(-4px) scale(1.028);
}

.symbol-result:not([aria-disabled="true"]):hover {
  transform: translateY(-2px) scale(1.015);
}

.icon-button:not(:disabled):hover {
  transform: translateY(-3px) scale(1.08);
}
```

推广时应继续保持这种分级，不建议给所有控件机械地使用同一个放大比例。

推荐范围：

- 小图标：`1.06–1.10`
- 普通按钮：`1.03–1.05`
- 大型行动按钮：`1.015–1.03`
- 密集列表：`1.01–1.02`

## 7. 桌面端与触摸端区分

悬停效果只在真正支持鼠标悬停的设备启用：

```css
@media (hover: hover) and (pointer: fine) {
  /* hover 动效 */
}
```

这可以避免：

- 手机上点击后按钮一直停留在 `hover` 状态；
- 触摸屏产生不自然的放大；
- 移动端滚动列表时误触发视觉反馈。

移动端依旧保留 `:active` 的按压反馈，因此触摸操作不会失去手感。

## 8. 禁用状态处理

真实不可操作按钮使用：

```html
<button disabled>提交</button>
```

自定义列表项可能使用：

```html
<button aria-disabled="true">不可交易股票</button>
```

本方案同时排除两种状态：

```css
button:not(:disabled):not([aria-disabled="true"])
```

禁用时高光被压低：

```css
button:disabled::before,
button[aria-disabled="true"]::before {
  opacity: .12;
}
```

注意：`aria-disabled="true"` 只表达无障碍语义，不会自动阻止点击。迁移时仍需在 JavaScript 事件中拦截对应操作。

## 9. 减少动态效果

对系统开启“减少动态效果”的用户，关闭动画和按钮缩放：

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation: none !important;
    transition: none !important;
  }

  button:not(:disabled):not([aria-disabled="true"]):hover,
  button:not(:disabled):not([aria-disabled="true"]):active {
    transform: none !important;
  }
}
```

玻璃颜色和边缘仍然存在，只是不再移动和缩放。

## 10. 轻量化与性能

这一版没有增加：

- JavaScript 监听器；
- 图片素材；
- SVG 滤镜；
- Canvas；
- WebGL；
- UI 框架；
- 动画依赖。

主要动画属性为：

- `transform`
- `opacity`
- `filter`
- `box-shadow`

其中 `transform` 和 `opacity` 通常最容易由浏览器合成处理。性能成本相对较高的是：

- `backdrop-filter`；
- 大面积模糊阴影；
- 同时存在大量动态玻璃控件。

推广时建议：

- 只给交互控件使用，不要给每个内容卡片都加动态玻璃。
- 表格中如果同时有几百个按钮，应使用较弱的阴影和较低的模糊强度。
- 不要永久设置 `will-change: transform`，否则大量按钮会长期占用合成层内存。
- 移动端可以把 `blur(16px)` 降至 `blur(10px)`。
- 低性能模式可以关闭 `backdrop-filter`，保留渐变、高光和缩放。

## 11. 推荐的推广方式

当前“极速下单”直接作用于所有 `button`。推广到复杂应用时，更推荐封装成独立类：

```html
<button class="liquid-control">保存</button>
<button class="liquid-control liquid-control--prominent">立即提交</button>
```

然后将选择器从：

```css
button { /* ... */ }
button::before { /* ... */ }
button:hover { /* ... */ }
```

改为：

```css
.liquid-control { /* ... */ }
.liquid-control::before { /* ... */ }
.liquid-control:hover { /* ... */ }
```

这样可以避免影响：

- 富文本编辑器内部按钮；
- 第三方组件；
- 日期选择器；
- 地图控件；
- 浏览器原生风格按钮；
- 极度密集的数据表格操作按钮。

推荐形成三个规格：

```css
.liquid-control--compact { /* 图标、工具栏 */ }
.liquid-control--standard { /* 普通按钮 */ }
.liquid-control--prominent { /* 提交、买入、卖出 */ }
```

## 12. 迁移检查清单

在其他应用中推广时，建议逐项检查：

- [ ] 是否只对需要的交互控件启用玻璃效果。
- [ ] 是否配置了适合品牌颜色的边缘和光晕变量。
- [ ] 大按钮、图标按钮和密集列表是否采用不同缩放比例。
- [ ] 伪元素是否设置了 `pointer-events: none`。
- [ ] 禁用按钮是否排除了悬停和按压动效。
- [ ] `aria-disabled` 控件是否在业务逻辑中真正拦截点击。
- [ ] 键盘焦点是否清晰可见。
- [ ] 是否支持 `prefers-reduced-motion`。
- [ ] 触摸设备是否避免使用悬停动画。
- [ ] Safari 是否提供 `-webkit-backdrop-filter`。
- [ ] 窄屏下按钮放大是否会被父容器裁切。
- [ ] 大量控件同时出现时是否仍能保持流畅。

## 13. 方案定义

这不是完整模拟真实光学折射的玻璃渲染器，而是一套以“双层高光、磨砂材质、浮起缩放和按压回弹”为核心的轻量交互视觉系统，适合迁移到交易终端、管理后台、仪表盘和本地工具。
