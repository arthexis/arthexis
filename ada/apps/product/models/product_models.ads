with Arthexis.ORM;

package Apps.Product.Models.Product_Models is
   --  Schema objects owned by the Product backbone app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create product entry point tables and seed core products.
end Apps.Product.Models.Product_Models;
