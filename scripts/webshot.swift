// webshot — HTML → PNG with the system WebKit (macOS). No browser and no screen needed:
// the page is laid out offscreen, exported as a vector PDF, then rasterized at `scale`.
// usage: webshot <in.html> <out.png> <css-width> [scale=2]
import AppKit
import WebKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 4, let cssWidth = Double(args[3]) else {
    fail("usage: webshot <in.html> <out.png> <css-width> [scale]")
}
let scale = args.count >= 5 ? (Double(args[4]) ?? 2) : 2
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])

func rasterize(_ pdf: Data, scale: Double) -> Data? {
    guard let rep = NSPDFImageRep(data: pdf) else { return nil }
    let w = Int((rep.bounds.width * scale).rounded()), h = Int((rep.bounds.height * scale).rounded())
    guard let bmp = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
          let ctx = NSGraphicsContext(bitmapImageRep: bmp) else { return nil }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    rep.draw(in: NSRect(x: 0, y: 0, width: w, height: h))
    NSGraphicsContext.restoreGraphicsState()
    return bmp.representation(using: .png, properties: [:])
}

final class Shot: NSObject, WKNavigationDelegate {
    let view: WKWebView

    init(width: Double) {
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: 100))
        view.appearance = NSAppearance(named: .aqua)   // cards are always the light theme
        super.init()
        view.navigationDelegate = self
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // wait for web fonts, then size the view to the whole page before exporting
        webView.callAsyncJavaScript("await document.fonts.ready; return Math.ceil(document.documentElement.getBoundingClientRect().height);",
                                    arguments: [:], in: nil, in: .page) { result in
            let height = CGFloat((try? result.get()) as? Double ?? 600)
            webView.frame.size.height = height
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                let cfg = WKPDFConfiguration()
                cfg.rect = NSRect(x: 0, y: 0, width: webView.frame.width, height: height)
                webView.createPDF(configuration: cfg) { pdf in
                    switch pdf {
                    case .success(let data):
                        guard let png = rasterize(data, scale: scale) else { fail("rasterize failed") }
                        do { try png.write(to: output); exit(0) } catch { fail("write: \(error)") }
                    case .failure(let error):
                        fail("pdf: \(error)")
                    }
                }
            }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("load: \(error)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail("load: \(error)")
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let shot = Shot(width: cssWidth)
shot.view.loadFileURL(input, allowingReadAccessTo: input.deletingLastPathComponent())
DispatchQueue.main.asyncAfter(deadline: .now() + 30) { fail("timeout") }
app.run()
