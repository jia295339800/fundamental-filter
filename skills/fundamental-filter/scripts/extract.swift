import Foundation
import PDFKit

guard CommandLine.arguments.count == 2,
      let pdf = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])),
      pdf.pageCount > 0 else {
    fputs("无法解析 PDF\n", stderr)
    exit(1)
}
for index in 0..<pdf.pageCount {
    print("=== PAGE \(index + 1) ===")
    print(pdf.page(at: index)?.string ?? "")
}
