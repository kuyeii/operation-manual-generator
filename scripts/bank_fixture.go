// Run from a copy inside the bank backend module, with output outside its source tree.
package main

import (
    "crypto/rand"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"

    "github.com/example/monorepo/backend/internal/cryptoenvelope"
    "github.com/xuri/excelize/v2"
)

func check(err error) { if err != nil { panic(err) } }
func workbook(rows [][]interface{}) []byte {
    f := excelize.NewFile()
    defer f.Close()
    for i, row := range rows { check(f.SetSheetRow("Sheet1", fmt.Sprintf("A%d", i+1), &row)) }
    buffer, err := f.WriteToBuffer(); check(err)
    return buffer.Bytes()
}
func main() {
    if len(os.Args) != 2 { panic("usage: fixture OUTPUT_DIRECTORY") }
    out := os.Args[1]
    check(os.MkdirAll(filepath.Join(out, "runtime-private"), 0700))
    medical := workbook([][]interface{}{
        {"psn_no", "psn_name", "phone", "id_card", "psn_clct_amt", "cashym"},
        {"SYN-001", "Synthetic Alpha", "TEST-PHONE-A", "TEST-ID-A", 6000, "202601"},
        {"SYN-002", "Synthetic Beta", "TEST-PHONE-B", "TEST-ID-B", 15000, "202601"},
        {"SYN-003", "Synthetic Gamma", "TEST-PHONE-C", "TEST-ID-C", 25000, "202601"},
    })
    bank := workbook([][]interface{}{
        {"bank_user_id", "name", "phone", "id_card"},
        {"BANK-A", "Synthetic Alpha", "TEST-PHONE-A", "TEST-ID-A"},
        {"BANK-B", "Synthetic Beta", "DIFFERENT-PHONE", "TEST-ID-B"},
        {"BANK-C", "Synthetic Gamma", "TEST-PHONE-C", "TEST-ID-C"},
    })
    key := make([]byte, 32); _, err := rand.Read(key); check(err)
    encrypted, err := cryptoenvelope.Encrypt(bank, key); check(err)
    secret, err := json.Marshal(map[string]string{"key": hex.EncodeToString(key)}); check(err)
    check(os.WriteFile(filepath.Join(out, "runtime-private", "synthetic.json"), secret, 0600))
    check(os.WriteFile(filepath.Join(out, "medical.xlsx"), medical, 0600))
    check(os.WriteFile(filepath.Join(out, "bank.xlsx.enc"), encrypted, 0600))
    expected, err := json.Marshal([][]interface{}{{"bank_user_id", "psn_name", "result"}, {"BANK-A", "Synthetic Alpha", 4}, {"BANK-B", "Synthetic Beta", 5}, {"BANK-C", "Synthetic Gamma", 8}}); check(err)
    check(os.WriteFile(filepath.Join(out, "expected.json"), expected, 0600))
    fmt.Println("Generated synthetic fixtures; key stored privately, not printed.")
}
