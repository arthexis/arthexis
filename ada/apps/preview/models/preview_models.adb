with Arthexis.ORM;

package body Apps.Preview.Models.Preview_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS preview_snapshot ("
         & "id INTEGER PRIMARY KEY, "
         & "product_name TEXT NOT NULL, "
         & "preview_name TEXT NOT NULL, "
         & "path TEXT NOT NULL, "
         & "template_name TEXT NOT NULL, "
         & "enabled INTEGER NOT NULL DEFAULT 1, "
         & "UNIQUE(product_name, preview_name), "
         & "FOREIGN KEY (product_name) REFERENCES product_product(product_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE INDEX IF NOT EXISTS idx_preview_snapshot_enabled "
         & "ON preview_snapshot (enabled, product_name, path);");
   end Install;

end Apps.Preview.Models.Preview_Models;
