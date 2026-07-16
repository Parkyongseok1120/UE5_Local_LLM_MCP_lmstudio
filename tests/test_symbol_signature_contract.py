from unreal_rag_mcp import symbol_signature_contract, symbol_signature_instruction


def test_symbol_signature_contract_forbids_convenience_overload_guess() -> None:
    contract = symbol_signature_contract("LoadStreamLevel")
    instruction = symbol_signature_instruction(contract)

    assert contract["mode"] == "exact_declaration"
    assert contract["query"] == "LoadStreamLevel"
    assert "parameter count" in contract["mustPreserve"]
    assert "omit a declared parameter" in contract["forbidden"]
    assert "Do not omit a required parameter or invent an overload" in instruction

