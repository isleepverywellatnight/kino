//! Windows: KINO's icon inside KINO.exe.
fn main() {
    println!("cargo:rerun-if-changed=kino.rc");
    println!("cargo:rerun-if-changed=../../assets/kino.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("kino.rc", embed_resource::NONE)
            .manifest_optional()
            .expect("KINO's icon");
    }
}
